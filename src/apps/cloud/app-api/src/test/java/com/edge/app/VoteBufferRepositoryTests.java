package com.edge.app;

import com.edge.app.entity.VoteChoice;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.app.repository.VoteCountRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(properties = "vote.mode=write-behind")
class VoteBufferRepositoryTests extends ContainerTests {
    @Autowired
    VoteBufferRepository buffer;
    @Autowired
    VoteCountRepository counts;
    @Autowired
    StringRedisTemplate redis;

    @Test
    void newVoteCountsAndMarksDirty() {
        long etf = 101;
        assertTrue(buffer.record(etf, 1L, VoteChoice.BUY));
        assertEquals(1, counts.counts(etf).buy());
        assertEquals(Map.of(1L, VoteChoice.BUY), buffer.readDirty(etf, 10));
        assertTrue(buffer.dirtyForecasts().contains(etf));
    }

    @Test
    void changedVoteMovesCounterAndOverwritesDirty() {
        long etf = 102;
        buffer.record(etf, 1L, VoteChoice.BUY);
        assertTrue(buffer.record(etf, 1L, VoteChoice.SELL));
        assertEquals(0, counts.counts(etf).buy());
        assertEquals(1, counts.counts(etf).sell());
        assertEquals(Map.of(1L, VoteChoice.SELL), buffer.readDirty(etf, 10));
    }

    @Test
    void sameChoiceIsNoOpAndDoesNotRedirty() {
        long etf = 103;
        buffer.record(etf, 1L, VoteChoice.HOLD);
        assertTrue(buffer.clearDirtyIfUnchanged(etf, 1L, VoteChoice.HOLD));
        assertFalse(buffer.record(etf, 1L, VoteChoice.HOLD));
        assertEquals(1, counts.counts(etf).hold());
        assertEquals(Map.of(), buffer.readDirty(etf, 10));
    }

    @Test
    void clearKeepsDirtyChangedDuringFlush() {
        long etf = 104;
        buffer.record(etf, 1L, VoteChoice.BUY);
        var snapshot = buffer.readDirty(etf, 10);
        buffer.record(etf, 1L, VoteChoice.SELL);
        assertFalse(buffer.clearDirtyIfUnchanged(etf, 1L, snapshot.get(1L)));
        assertEquals(Map.of(1L, VoteChoice.SELL), buffer.readDirty(etf, 10));
        assertFalse(buffer.releaseIfClean(etf));
        assertTrue(buffer.clearDirtyIfUnchanged(etf, 1L, VoteChoice.SELL));
        assertTrue(buffer.releaseIfClean(etf));
        assertFalse(buffer.dirtyForecasts().contains(etf));
    }

    @Test
    void readDirtyHonoursBatchSize() {
        long etf = 105;
        for (long user = 1; user <= 5; user++) {
            buffer.record(etf, user, VoteChoice.BUY);
        }
        assertEquals(2, buffer.readDirty(etf, 2).size());
        assertEquals(5, buffer.readDirty(etf, 10).size());
    }

    @Test
    void concurrentRecordsKeepCountersConsistent() throws Exception {
        long etf = 106;
        try (var pool = Executors.newFixedThreadPool(8)) {
            List<Callable<Boolean>> jobs = new ArrayList<>();
            for (int i = 0; i < 32; i++) {
                VoteChoice choice = VoteChoice.values()[i % 3];
                jobs.add(() -> buffer.record(etf, 1L, choice));
            }
            for (var result : pool.invokeAll(jobs)) {
                result.get();
            }
        }
        var c = counts.counts(etf);
        assertEquals(1, c.buy() + c.hold() + c.sell());
        assertEquals(1, buffer.readDirty(etf, 10).size());
    }
}
