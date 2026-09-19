package com.edge.app;

import com.edge.app.entity.VoteChoice;
import com.edge.app.repository.VoteBufferRepository;
import com.edge.app.repository.VoteRepository;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.web.client.RestClient;

import java.util.Map;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.doAnswer;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"vote.mode=write-behind", "vote.flush.interval-ms=3600000"})
class WriteBehindVoteServiceTests extends ContainerTests {
    @LocalServerPort
    int port;
    @MockitoSpyBean
    VoteBufferRepository buffer;
    @Autowired
    VoteRepository voteRepository;
    @Autowired
    MeterRegistry meterRegistry;

    RestClient client() {
        return RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build();
    }
    int vote(long etf, long user, String choice) {
        return client().post().uri("/api/v1/forecasts/" + etf + "/votes").header("X-User-Id", Long.toString(user))
                .body(Map.of("choice", choice)).retrieve().toBodilessEntity().getStatusCode().value();
    }
    long dbRows(long etf) {
        return voteRepository.findAll().stream().filter(v -> v.getForecastId() == etf).count();
    }

    @Test
    void voteLandsInBufferWithoutDbTransactionOrRow() {
        long etf = 301;
        doAnswer(invocation -> {
            assertFalse(TransactionSynchronizationManager.isActualTransactionActive());
            return invocation.callRealMethod();
        }).when(buffer).record(etf, 1L, VoteChoice.BUY);
        assertEquals(200, vote(etf, 1, "BUY"));
        assertEquals(0, dbRows(etf));
        String body = client().get().uri("/api/v1/forecasts/" + etf + "/votes/count").retrieve().body(String.class);
        assertTrue(body.contains("\"buy\":1") && body.contains("\"source\":\"redis\""), body);
    }

    @Test
    void redisOutageFailsFastWith503() throws Exception {
        long etf = 302;
        assertEquals(200, vote(etf, 9, "HOLD"));
        double before = meterRegistry.counter("vote.redis.write.failures").count();
        REDIS.getDockerClient().pauseContainerCmd(REDIS.getContainerId()).exec();
        try {
            long start = System.nanoTime();
            assertEquals(503, vote(etf, 1, "BUY"));
            assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start) < 1000);
        } finally {
            REDIS.getDockerClient().unpauseContainerCmd(REDIS.getContainerId()).exec();
        }
        assertTrue(meterRegistry.counter("vote.redis.write.failures").count() > before);
        assertEquals(0, dbRows(etf));
    }
}
