package com.edge.app;

import com.edge.app.entity.Vote;
import com.edge.app.entity.VoteChoice;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.app.repository.VoteFlushRepository;
import com.edge.app.repository.VoteRepository;
import com.edge.app.service.VoteFlusher;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
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

    Map<Long, VoteChoice> dbVotes(long etf) {
        return voteRepository.findAll().stream().filter(v -> v.getForecastId() == etf)
                .collect(java.util.stream.Collectors.toMap(Vote::getUserId, Vote::getChoice));
    }

    @Test
    void flushWritesDirtyVotesToDbAndReleasesForecast() {
        long etf = 201;
        buffer.record(etf, 1L, VoteChoice.BUY);
        buffer.record(etf, 2L, VoteChoice.SELL);
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.BUY, 2L, VoteChoice.SELL), dbVotes(etf));
        assertEquals(Map.of(), buffer.readDirty(etf, 10));
        assertFalse(buffer.dirtyForecasts().contains(etf));
    }

    @Test
    void flushDrainsMoreThanOneBatchAcrossRuns() {
        long etf = 202;
        for (long user = 1; user <= 5; user++) {
            buffer.record(etf, user, VoteChoice.HOLD);
        }
        flusher.flush();
        assertEquals(2, dbVotes(etf).size());
        assertTrue(buffer.dirtyForecasts().contains(etf));
        flusher.flush();
        flusher.flush();
        assertEquals(5, dbVotes(etf).size());
        assertFalse(buffer.dirtyForecasts().contains(etf));
    }

    @Test
    void voteChangedDuringFlushStaysDirtyAndLandsNextRun() {
        long etf = 203;
        buffer.record(etf, 1L, VoteChoice.BUY);
        doAnswer(invocation -> {
            invocation.callRealMethod();
            buffer.record(etf, 1L, VoteChoice.SELL);
            return null;
        }).when(flushRepository).upsertAll(eq(etf), any());
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.BUY), dbVotes(etf));
        assertEquals(Map.of(1L, VoteChoice.SELL), buffer.readDirty(etf, 10));
        assertTrue(buffer.dirtyForecasts().contains(etf));
        doAnswer(invocation -> invocation.callRealMethod()).when(flushRepository).upsertAll(eq(etf), any());
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.SELL), dbVotes(etf));
        assertFalse(buffer.dirtyForecasts().contains(etf));
    }

    @Test
    void reflushAfterCrashBetweenUpsertAndClearIsIdempotent() {
        long etf = 204;
        buffer.record(etf, 1L, VoteChoice.BUY);
        flushRepository.upsertAll(etf, Map.of(1L, VoteChoice.BUY));
        flusher.flush();
        assertEquals(1, voteRepository.findAll().stream().filter(v -> v.getForecastId() == etf).count());
        assertEquals(Map.of(1L, VoteChoice.BUY), dbVotes(etf));
        assertFalse(buffer.dirtyForecasts().contains(etf));
    }

    @Test
    void dbFailureKeepsDirtyForNextRun() {
        long etf = 205;
        buffer.record(etf, 1L, VoteChoice.BUY);
        doThrow(new RuntimeException("db down")).when(flushRepository).upsertAll(eq(etf), any());
        double before = meterRegistry.counter("vote.flush.failures").count();
        flusher.flush();
        assertEquals(before + 1, meterRegistry.counter("vote.flush.failures").count());
        assertEquals(Map.of(1L, VoteChoice.BUY), buffer.readDirty(etf, 10));
        assertTrue(buffer.dirtyForecasts().contains(etf));
        assertEquals(Map.of(), dbVotes(etf));
        doAnswer(invocation -> invocation.callRealMethod()).when(flushRepository).upsertAll(eq(etf), any());
        flusher.flush();
        assertEquals(Map.of(1L, VoteChoice.BUY), dbVotes(etf));
    }
}
