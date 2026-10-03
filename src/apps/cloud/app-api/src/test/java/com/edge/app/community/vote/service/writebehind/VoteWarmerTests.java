package com.edge.app.community.vote.service.writebehind;

import com.edge.app.community.vote.repository.writebehind.VoteBufferRepository;
import com.edge.app.community.vote.repository.writebehind.VoteFlushRepository;

import com.edge.app.ContainerTests;
import com.edge.app.community.vote.repository.VoteCounts;
import com.edge.app.community.vote.entity.VoteChoice;
import com.edge.app.community.vote.repository.VoteCountRepository;
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
        String etf = "000401";
        flushRepository.upsertAll(etf, Map.of(1L, VoteChoice.BUY, 2L, VoteChoice.WAIT));
        warmer.warm();
        assertEquals(new VoteCounts(1, 1, 0), counts.counts(etf));
        assertEquals(2, redis.opsForHash().size("vote:{" + etf + "}:choices"));
        buffer.record(etf, 1L, VoteChoice.SELL);
        assertEquals(new VoteCounts(0, 1, 1), counts.counts(etf));
    }

    @Test
    void warmMergesMissingUsersWithoutOverridingLiveOnes() {
        String etf = "000402";
        buffer.record(etf, 1L, VoteChoice.BUY);
        // DB 의 user 1 낡은 표는 무시하고 Redis 에 없는 user 2 표만 채움
        flushRepository.upsertAll(etf, Map.of(1L, VoteChoice.SELL, 2L, VoteChoice.WAIT));
        warmer.warm();
        assertEquals(new VoteCounts(1, 1, 0), counts.counts(etf));
        assertEquals("buy", redis.opsForHash().get("vote:{" + etf + "}:choices", "1"));
        assertEquals(Map.of(1L, VoteChoice.BUY), buffer.readDirty(etf, 10));
    }

    @Test
    void reconnectWarmsLostForecasts() throws Exception {
        String etf = "000403";
        flushRepository.upsertAll(etf, Map.of(1L, VoteChoice.SELL));
        REDIS.execInContainer("redis-cli", "FLUSHALL");
        REDIS.execInContainer("redis-cli", "CLIENT", "KILL", "TYPE", "normal", "SKIPME", "yes");
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(30);
        boolean restored = false;
        while (System.nanoTime() < deadline) {
            try {
                if (counts.counts(etf).sells() == 1 && redis.opsForHash().size("vote:{" + etf + "}:choices") == 1) {
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
