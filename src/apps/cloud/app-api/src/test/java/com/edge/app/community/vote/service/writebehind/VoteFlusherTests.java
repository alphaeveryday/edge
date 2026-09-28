package com.edge.app.community.vote.service.writebehind;

import com.edge.app.community.vote.repository.writebehind.VoteBufferRepository;
import com.edge.app.community.vote.repository.writebehind.VoteFlushRepository;

import com.edge.app.ContainerTests;
import com.edge.app.community.vote.entity.Vote;
import com.edge.app.community.vote.entity.VoteChoice;
import com.edge.app.community.vote.repository.VoteRepository;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;

@SpringBootTest(properties = {"vote.mode=write-behind", "vote.flush.interval-ms=3600000", "vote.flush.batch-size=2"})
class VoteFlusherTests extends ContainerTests {
    @Autowired
    VoteFlusher flusher;
    @Autowired
    VoteBufferRepository buffer;
    @MockitoSpyBean
    VoteFlushRepository flushRepository;
    @Autowired
    VoteRepository voteRepository;
    @Autowired
    MeterRegistry meterRegistry;
    @Autowired
    StringRedisTemplate redis;

    Map<Long, VoteChoice> dbVotes(String etf) {
        return voteRepository.findAll().stream().filter(v -> v.getEtfCode().equals(etf))
                .collect(java.util.stream.Collectors.toMap(Vote::getMemberId, Vote::getChoice));
    }

    @Test
    void flushWritesDirtyVotesToDbAndReleasesForecast() {
        String etf = "000201";
        buffer.record(etf, 1L, VoteChoice.BUY);
        buffer.record(etf, 2L, VoteChoice.SELL);
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.BUY, 2L, VoteChoice.SELL), dbVotes(etf));
        assertEquals(Map.of(), buffer.readDirty(etf, 10));
        assertFalse(buffer.dirtyEtfs().contains(etf));
    }

    @Test
    void flushDrainsMoreThanOneBatchAcrossRuns() {
        String etf = "000202";
        for (long member = 1; member <= 5; member++) {
            buffer.record(etf, member, VoteChoice.WAIT);
        }
        flusher.flush();
        assertEquals(2, dbVotes(etf).size());
        assertTrue(buffer.dirtyEtfs().contains(etf));
        flusher.flush();
        flusher.flush();
        assertEquals(5, dbVotes(etf).size());
        assertFalse(buffer.dirtyEtfs().contains(etf));
    }

    @Test
    void voteChangedDuringFlushStaysDirtyAndLandsNextRun() {
        String etf = "000203";
        buffer.record(etf, 1L, VoteChoice.BUY);
        doAnswer(invocation -> {
            invocation.callRealMethod();
            buffer.record(etf, 1L, VoteChoice.SELL);
            return null;
        }).when(flushRepository).upsertAll(eq(etf), any());
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.BUY), dbVotes(etf));
        assertEquals(Map.of(1L, VoteChoice.SELL), buffer.readDirty(etf, 10));
        assertTrue(buffer.dirtyEtfs().contains(etf));
        doAnswer(invocation -> invocation.callRealMethod()).when(flushRepository).upsertAll(eq(etf), any());
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.SELL), dbVotes(etf));
        assertFalse(buffer.dirtyEtfs().contains(etf));
    }

    @Test
    void reflushAfterCrashBetweenUpsertAndClearIsIdempotent() {
        String etf = "000204";
        buffer.record(etf, 1L, VoteChoice.BUY);
        flushRepository.upsertAll(etf, Map.of(1L, VoteChoice.BUY));
        flusher.flush();
        assertEquals(1, voteRepository.findAll().stream().filter(v -> v.getEtfCode().equals(etf)).count());
        assertEquals(Map.of(1L, VoteChoice.BUY), dbVotes(etf));
        assertFalse(buffer.dirtyEtfs().contains(etf));
    }

    @Test
    void corruptForecastDoesNotBlockOtherForecasts() {
        String bad = "000206", good = "000207";
        redis.opsForHash().put("vote:{" + bad + "}:dirty", "not-a-user", "buy");
        redis.opsForSet().add("vote:dirty-etfs", bad);
        buffer.record(good, 1L, VoteChoice.SELL);
        double before = meterRegistry.counter("vote.flush.failures").count();
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.SELL), dbVotes(good));
        assertFalse(buffer.dirtyEtfs().contains(good));
        assertEquals(before + 1, meterRegistry.counter("vote.flush.failures").count());
        redis.delete("vote:{" + bad + "}:dirty");
        redis.opsForSet().remove("vote:dirty-etfs", bad);
    }

    @Test
    void dbFailureKeepsDirtyForNextRun() {
        String etf = "000205";
        buffer.record(etf, 1L, VoteChoice.BUY);
        doThrow(new RuntimeException("db down")).when(flushRepository).upsertAll(eq(etf), any());
        double before = meterRegistry.counter("vote.flush.failures").count();
        flusher.flush();
        assertEquals(before + 1, meterRegistry.counter("vote.flush.failures").count());
        assertEquals(Map.of(1L, VoteChoice.BUY), buffer.readDirty(etf, 10));
        assertTrue(buffer.dirtyEtfs().contains(etf));
        assertEquals(Map.of(), dbVotes(etf));
        doAnswer(invocation -> invocation.callRealMethod()).when(flushRepository).upsertAll(eq(etf), any());
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.BUY), dbVotes(etf));
    }
}
