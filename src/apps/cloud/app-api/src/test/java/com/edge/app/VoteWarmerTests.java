package com.edge.app;

import com.edge.app.dto.VoteCounts;
import com.edge.app.entity.VoteChoice;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.app.repository.VoteCountRepository;
import com.edge.app.repository.VoteFlushRepository;
import com.edge.app.service.VoteWarmer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.util.Map;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(properties = {"vote.mode=write-behind", "vote.flush.interval-ms=3600000"})
class VoteWarmerTests extends ContainerTests {
    @Autowired
    VoteWarmer warmer;
    @Autowired
    VoteBufferRepository buffer;
    @Autowired
    VoteCountRepository counts;
    @Autowired
    VoteFlushRepository flushRepository;
    @Autowired
    StringRedisTemplate redis;

    @Test
    void warmLoadsForecastMissingFromRedis() {
        long etf = 401;
        flushRepository.upsertAll(etf, Map.of(1L, VoteChoice.BUY, 2L, VoteChoice.HOLD));
        warmer.warm();
        assertEquals(new VoteCounts(1, 1, 0), counts.counts(etf));
        assertEquals(2, redis.opsForHash().size("vote:{" + etf + "}:choices"));
        buffer.record(etf, 1L, VoteChoice.SELL);
        assertEquals(new VoteCounts(0, 1, 1), counts.counts(etf));
    }

    @Test
    void warmSkipsForecastAlreadyLiveInRedis() {
        long etf = 402;
        buffer.record(etf, 1L, VoteChoice.BUY);
        flushRepository.upsertAll(etf, Map.of(2L, VoteChoice.HOLD));
        warmer.warm();
        assertEquals(new VoteCounts(1, 0, 0), counts.counts(etf));
        assertEquals(1, redis.opsForHash().size("vote:{" + etf + "}:choices"));
    }

    @Test
    void reconnectWarmsLostForecasts() throws Exception {
        long etf = 403;
        flushRepository.upsertAll(etf, Map.of(1L, VoteChoice.SELL));
        REDIS.execInContainer("redis-cli", "FLUSHALL");
        REDIS.execInContainer("redis-cli", "CLIENT", "KILL", "TYPE", "normal", "SKIPME", "yes");
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(30);
        boolean restored = false;
        while (System.nanoTime() < deadline) {
            try {
                if (counts.counts(etf).sell() == 1 && redis.opsForHash().size("vote:{" + etf + "}:choices") == 1) {
                    restored = true;
                    break;
                }
            } catch (Exception ignored) {
            }
            Thread.sleep(100);
        }
        assertTrue(restored, "Reconnect must reload choices and count from DB");
    }
}
