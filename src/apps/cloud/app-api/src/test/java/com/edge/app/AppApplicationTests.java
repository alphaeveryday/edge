package com.edge.app;

import com.edge.app.community.vote.dto.VoteCountResponse;
import com.edge.app.community.vote.repository.VoteCounts;
import com.edge.app.community.vote.entity.Vote;
import com.edge.app.community.vote.entity.VoteChoice;
import com.edge.app.community.vote.repository.VoteCountRepository;
import com.edge.app.community.vote.repository.VoteRepository;
import com.edge.app.common.auth.AccessTokens;
import com.edge.app.community.vote.service.VoteService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.web.client.RestClient;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.doAnswer;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "vote.admin-token=test-admin", "vote.reconcile.initial-delay=PT1H",
        "app.jwt.secret=test-jwt-secret-for-tests-only-32bytes"})
class AppApplicationTests {
    @ServiceConnection
    static final PostgreSQLContainer POSTGRES = new PostgreSQLContainer(DockerImageName.parse("postgres:16"));
    @ServiceConnection(name = "redis")
    static final GenericContainer<?> REDIS =
            new GenericContainer<>(DockerImageName.parse("redis:7-alpine")).withExposedPorts(6379);
    static {
        POSTGRES.start();
        REDIS.start();
    }

    @LocalServerPort
    int port;
    @Autowired
    VoteService voteService;
    @MockitoSpyBean
    VoteCountRepository voteCountRepository;
    @Autowired
    VoteRepository voteRepository;
    @Autowired
    StringRedisTemplate redis;
    @Autowired
    AccessTokens tokens;

    RestClient client() {
        return RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build();
    }
    List<Vote> votes(String etf) {
        return voteRepository.findAll().stream().filter(v -> v.getEtfCode().equals(etf)).toList();
    }
    String bearer(long member) {
        return "Bearer " + tokens.issue(member);
    }
    int vote(String etf, long member, Object choice) {
        return client().put().uri("/api/v1/etfs/" + etf + "/vote").header("Authorization", bearer(member))
                .body(Map.of("choice", choice)).retrieve().toBodilessEntity().getStatusCode().value();
    }

    @Test
    void voteIsCommittedBeforeRedisIsCalled() throws Exception {
        String etf = "000011";
        // 리스너 안의 단언은 AFTER_COMMIT 콜백이 삼킨다 — 관측값을 테스트 스레드로 가져와 여기서 단언한다.
        var seenAtRedisCall = new CompletableFuture<Integer>();
        doAnswer(invocation -> {
            try (var pool = Executors.newSingleThreadExecutor()) {
                seenAtRedisCall.complete(pool.submit(() -> votes(etf).size()).get(5, TimeUnit.SECONDS));
            }
            return invocation.callRealMethod();
        }).when(voteCountRepository).vote(etf, 1L, VoteChoice.BUY);
        assertEquals(200, vote(etf, 1, "buy"));
        assertEquals(1, seenAtRedisCall.get(5, TimeUnit.SECONDS), "vote must be visible in DB before Redis is called");
    }

    @Test
    void numericChoiceIsRejected() {
        assertEquals(400, vote("000012", 1, 0));
        assertTrue(votes("000012").isEmpty());
    }

    @Test
    void concurrentRevotesConvergeToSingleLastChoice() throws Exception {
        String etf = "000022";
        try (var pool = Executors.newFixedThreadPool(8)) {
            List<Callable<Integer>> jobs = new ArrayList<>();
            for (int i = 0; i < 16; i++) {
                jobs.add(() -> vote(etf, 1, "buy"));
            }
            for (var result : pool.invokeAll(jobs)) {
                assertEquals(200, result.get());
            }
        }
        assertEquals(1, voteCountRepository.counts(etf).buys());
        // 재투표는 마지막 선택으로 변경 — 이전 카운터에서 빠지고 새 카운터로 옮겨진다.
        assertEquals(200, vote(etf, 1, "sell"));
        var rows = votes(etf);
        assertEquals(1, rows.size());
        assertEquals(VoteChoice.SELL, rows.get(0).getChoice());
        assertEquals(new VoteCounts(0, 0, 1), voteCountRepository.counts(etf));
        // 같은 선택 재실행(재시도·스크립트 재실행)은 no-op — 중복 집계 없음.
        voteCountRepository.vote(etf, 1L, VoteChoice.SELL);
        assertEquals(1, voteCountRepository.counts(etf).sells());
        try (var connection = redis.getConnectionFactory().getConnection()) {
            connection.scriptingCommands().scriptFlush();
        }
        assertEquals(200, vote(etf, 2, "wait"));
        assertEquals(1, voteCountRepository.counts(etf).waits());
    }

    @Test
    void outageKeepsCommittedVotesAndReconnectRepairs() throws Exception {
        String etf = "000033";
        assertEquals(200, vote(etf, 1, "buy"));
        voteCountRepository.replace(etf, votes(etf));
        REDIS.getDockerClient().pauseContainerCmd(REDIS.getContainerId()).exec();
        try {
            long start = System.nanoTime();
            assertEquals(200, vote(etf, 2, "sell"));
            assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start) < 1000);
            // 장애 중 재투표(변경)도 DB 에는 반영된다 — 폴백 집계가 마지막 선택을 보여준다.
            assertEquals(200, vote(etf, 2, "wait"));
            assertEquals(new VoteCountResponse(1, 1, 0, "db"), voteService.counts(etf));
        } finally {
            REDIS.getDockerClient().unpauseContainerCmd(REDIS.getContainerId()).exec();
        }
        REDIS.execInContainer("redis-cli", "FLUSHALL");
        REDIS.execInContainer("redis-cli", "CLIENT", "KILL", "TYPE", "normal", "SKIPME", "yes");
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(30);
        boolean restored = false;
        while (System.nanoTime() < deadline) {
            try {
                var count = voteCountRepository.counts(etf);
                if (count.buys() == 1 && count.waits() == 1 && redis.opsForHash().size("vote:{" + etf + "}:choices") == 2) {
                    restored = true;
                    break;
                }
            } catch (Exception ignored) {
            }
            Thread.sleep(100);
        }
        assertTrue(restored, "Reconnect must rebuild both count and choices from committed DB records");
    }

    @Test
    void manualRepairRequiresAdminAndRepairsBothDerivedKeys() throws Exception {
        String etf = "000044";
        assertEquals(400, vote(etf, 1, "INVALID"));
        // 회원 전용이라 토큰 없는 투표는 COMMON401.
        assertEquals(401, client().put().uri("/api/v1/etfs/" + etf + "/vote")
                .body(Map.of("choice", "buy")).retrieve().toBodilessEntity().getStatusCode().value());
        assertEquals(400, client().put().uri("/api/v1/etfs/" + etf + "/vote").header("Authorization", bearer(1))
                .body(Map.of()).retrieve().toBodilessEntity().getStatusCode().value());
        assertEquals(200, vote(etf, 1, "buy"));
        redis.opsForHash().put("vote:{" + etf + "}:count", "buy", "99");
        assertEquals(403, client().post().uri("/api/v1/admin/votes/reconcile").retrieve().toBodilessEntity().getStatusCode().value());
        assertEquals(200, client().post().uri("/api/v1/admin/votes/reconcile").header("X-Admin-Token", "test-admin").retrieve().toBodilessEntity().getStatusCode().value());
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (voteCountRepository.counts(etf).buys() != 1 && System.nanoTime() < deadline) {
            Thread.sleep(50);
        }
        assertEquals(1, voteCountRepository.counts(etf).buys());
    }
}
