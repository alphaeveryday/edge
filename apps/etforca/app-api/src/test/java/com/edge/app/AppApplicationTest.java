package com.edge.app;

import com.edge.app.common.auth.AccessTokens;
import com.edge.app.community.vote.dto.VoteCountResponse;
import com.edge.app.community.vote.entity.Vote;
import com.edge.app.community.vote.entity.VoteChoice;
import com.edge.app.community.vote.repository.VoteCountRepository;
import com.edge.app.community.vote.repository.VoteCounts;
import com.edge.app.community.vote.repository.VoteRepository;
import com.edge.app.community.vote.service.VoteService;
import com.edge.app.support.stereotype.WebApiAdapterTest;
import com.zaxxer.hikari.HikariDataSource;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import org.testcontainers.containers.GenericContainer;
import tools.jackson.databind.ObjectMapper;

import javax.sql.DataSource;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.doAnswer;

@WebApiAdapterTest
@TestPropertySource(properties = {"vote.admin-token=test-admin", "vote.reconcile.initial-delay=PT1H"})
@MockitoSpyBean(types = VoteCountRepository.class)
record AppApplicationTest(MockMvcTester mvcTester, ObjectMapper objectMapper, VoteService voteService,
        VoteCountRepository voteCountRepository, VoteRepository voteRepository, StringRedisTemplate redis, AccessTokens tokens,
        DataSource dataSource, @Qualifier("redis") GenericContainer<?> redisContainer) {
    @Test
    void voteIsCommittedAndConnectionReleasedBeforeRedisIsCalled() throws Exception {
        String etf = "000011";
        // 요청 스레드 안 단언 대신 Redis 호출 시점 관측값의 테스트 스레드 단언
        var seenAtRedisCall = new CompletableFuture<Integer>();
        var activeAtRedisCall = new CompletableFuture<Integer>();
        doAnswer(invocation -> {
            activeAtRedisCall.complete(dataSource.unwrap(HikariDataSource.class).getHikariPoolMXBean().getActiveConnections());
            try (var pool = Executors.newSingleThreadExecutor()) {
                seenAtRedisCall.complete(pool.submit(() -> votes(etf).size()).get(5, TimeUnit.SECONDS));
            }
            return invocation.callRealMethod();
        }).when(voteCountRepository).vote(etf, 1L, VoteChoice.BUY);
        assertThat(vote(etf, 1, "buy")).isEqualTo(200);
        assertThat(activeAtRedisCall.get(5, TimeUnit.SECONDS)).as("DB connection must be released before Redis is called").isEqualTo(0);
        assertThat(seenAtRedisCall.get(5, TimeUnit.SECONDS)).as("vote must be visible in DB before Redis is called").isEqualTo(1);
    }

    @Test
    void myVoteFollowsLastChoiceAndIsNullBeforeVoting() {
        String etf = "000066";
        assertThat(myVote(etf, 1)).isNull();
        assertThat(vote(etf, 1, "buy")).isEqualTo(200);
        assertThat(vote(etf, 1, "sell")).isEqualTo(200);
        assertThat(myVote(etf, 1)).isEqualTo("sell");
        assertThat(myVote(etf, 2)).isNull();
        assertThat(mvcTester.get().uri("/api/v1/etfs/" + etf + "/vote").exchange()).hasStatus(401);
    }

    @Test
    void withdrawRemovesVoteFromDbAndCountsIdempotently() {
        String etf = "000077";
        assertThat(vote(etf, 1, "buy")).isEqualTo(200);
        assertThat(vote(etf, 2, "sell")).isEqualTo(200);
        assertThat(withdraw(etf, 1)).isEqualTo(200);
        assertThat(votes(etf)).hasSize(1);
        assertThat(myVote(etf, 1)).isNull();
        assertThat(voteCountRepository.counts(etf)).isEqualTo(new VoteCounts(0, 0, 1));
        assertThat(redis.opsForHash().size("vote:{" + etf + "}:choices")).isEqualTo(1);
        // 투표 기록 없는 재철회의 카운터 불변
        assertThat(withdraw(etf, 1)).isEqualTo(200);
        assertThat(voteCountRepository.counts(etf)).isEqualTo(new VoteCounts(0, 0, 1));
        assertThat(mvcTester.delete().uri("/api/v1/etfs/" + etf + "/vote").exchange()).hasStatus(401);
    }

    @Test
    void numericChoiceIsRejected() {
        assertThat(vote("000012", 1, 0)).isEqualTo(400);
        assertThat(votes("000012")).isEmpty();
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
                assertThat(result.get()).isEqualTo(200);
            }
        }
        assertThat(voteCountRepository.counts(etf).buys()).isEqualTo(1);
        // 이전 카운터에서 새 카운터로 옮겨지는 마지막 선택 기준의 재투표
        assertThat(vote(etf, 1, "sell")).isEqualTo(200);
        var rows = votes(etf);
        assertThat(rows).hasSize(1);
        assertThat(rows.get(0).getChoice()).isEqualTo(VoteChoice.SELL);
        assertThat(voteCountRepository.counts(etf)).isEqualTo(new VoteCounts(0, 0, 1));
        // 중복 집계 없는 같은 선택 재실행
        voteCountRepository.vote(etf, 1L, VoteChoice.SELL);
        assertThat(voteCountRepository.counts(etf).sells()).isEqualTo(1);
        try (var connection = redis.getConnectionFactory().getConnection()) {
            connection.scriptingCommands().scriptFlush();
        }
        assertThat(vote(etf, 2, "wait")).isEqualTo(200);
        assertThat(voteCountRepository.counts(etf).waits()).isEqualTo(1);
    }

    @Test
    void outageKeepsCommittedVotesAndReconnectRepairs() throws Exception {
        String etf = "000033";
        assertThat(vote(etf, 1, "buy")).isEqualTo(200);
        voteCountRepository.replace(etf, votes(etf));
        redisContainer.getDockerClient().pauseContainerCmd(redisContainer.getContainerId()).exec();
        try {
            long start = System.nanoTime();
            assertThat(vote(etf, 2, "sell")).isEqualTo(200);
            assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)).isLessThan(1000);
            // 장애 중 재투표의 DB 반영과 폴백 집계의 마지막 선택 표시
            assertThat(vote(etf, 2, "wait")).isEqualTo(200);
            assertThat(voteService.counts(etf)).isEqualTo(new VoteCountResponse(1, 1, 0, "db"));
        } finally {
            redisContainer.getDockerClient().unpauseContainerCmd(redisContainer.getContainerId()).exec();
        }
        redisContainer.execInContainer("redis-cli", "FLUSHALL");
        redisContainer.execInContainer("redis-cli", "CLIENT", "KILL", "TYPE", "normal", "SKIPME", "yes");
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
        assertThat(restored).as("Reconnect must rebuild both count and choices from committed DB records").isTrue();
    }

    @Test
    void manualRepairRequiresAdminAndRepairsBothDerivedKeys() throws Exception {
        String etf = "000044";
        assertThat(vote(etf, 1, "INVALID")).isEqualTo(400);
        // 회원 전용이라 토큰 없는 투표의 COMMON401 응답
        assertThat(mvcTester.put().uri("/api/v1/etfs/" + etf + "/vote")
                .contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(Map.of("choice", "buy"))).exchange())
                .hasStatus(401);
        assertThat(mvcTester.put().uri("/api/v1/etfs/" + etf + "/vote").header("Authorization", bearer(1))
                .contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(Map.of())).exchange())
                .hasStatus(400);
        assertThat(vote(etf, 1, "buy")).isEqualTo(200);
        redis.opsForHash().put("vote:{" + etf + "}:count", "buy", "99");
        assertThat(mvcTester.post().uri("/api/v1/admin/votes/reconcile").exchange()).hasStatus(403);
        assertThat(mvcTester.post().uri("/api/v1/admin/votes/reconcile").header("X-Admin-Token", "test-admin").exchange()).hasStatusOk();
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (voteCountRepository.counts(etf).buys() != 1 && System.nanoTime() < deadline) {
            Thread.sleep(50);
        }
        assertThat(voteCountRepository.counts(etf).buys()).isEqualTo(1);
    }

    private List<Vote> votes(String etf) {
        return voteRepository.findAll().stream().filter(v -> v.getEtfCode().equals(etf)).toList();
    }

    private String bearer(long member) {
        return "Bearer " + tokens.issue(member);
    }

    private int vote(String etf, long member, Object choice) {
        return mvcTester.put().uri("/api/v1/etfs/" + etf + "/vote").header("Authorization", bearer(member))
                .contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(Map.of("choice", choice)))
                .exchange().getResponse().getStatus();
    }

    private Object myVote(String etf, long member) {
        MvcTestResult res = mvcTester.get().uri("/api/v1/etfs/" + etf + "/vote").header("Authorization", bearer(member)).exchange();
        Map<String, Object> vote = result(res);
        return vote.get("choice");
    }

    private int withdraw(String etf, long member) {
        return mvcTester.delete().uri("/api/v1/etfs/" + etf + "/vote").header("Authorization", bearer(member))
                .exchange().getResponse().getStatus();
    }
}
