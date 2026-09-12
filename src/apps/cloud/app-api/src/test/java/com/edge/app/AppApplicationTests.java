package com.edge.app;

import com.edge.app.repository.RedisRebuildRepository;
import com.edge.app.service.RebuildService;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.client.RestClient;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class AppApplicationTests {

	@ServiceConnection
	static final PostgreSQLContainer POSTGRES =
			new PostgreSQLContainer(DockerImageName.parse("postgres:16-alpine"));

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
	RebuildService rebuildService;

	@Autowired
	RedisRebuildRepository redisRebuildRepository;

	@Autowired
	StringRedisTemplate redisTemplate;

	@Autowired
	TransactionTemplate transactionTemplate;

	@Autowired
	CircuitBreaker redisCircuitBreaker;

	private RestClient client() {
		return RestClient.builder()
				.baseUrl("http://localhost:" + port)
				.defaultStatusHandler(code -> true, (request, response) -> {
				})
				.build();
	}

	@Test
	void 발행_투표_철회_정상_경로() {
		RestClient client = client();

		ResponseEntity<Map> published = client.post().uri("/api/forecasts")
				.body(Map.of(
						"ticker", "069500",
						"direction", "UP",
						"endAt", Instant.now().plusSeconds(3600).toString(),
						"rationale", "테스트 근거"))
				.retrieve().toEntity(Map.class);
		assertEquals(200, published.getStatusCode().value());
		assertEquals(true, published.getBody().get("isSuccess"));
		long id = ((Number) result(published).get("id")).longValue();

		ResponseEntity<Map> vote1 = vote(client, id, 1, "AGREE");
		assertEquals(200, vote1.getStatusCode().value());
		assertEquals(1, result(vote1).get("agree"));
		assertEquals(0, result(vote1).get("disagree"));

		ResponseEntity<Map> changed = vote(client, id, 1, "DISAGREE");
		assertEquals(0, result(changed).get("agree"));
		assertEquals(1, result(changed).get("disagree"));

		ResponseEntity<Map> vote2 = vote(client, id, 2, "AGREE");
		assertEquals(1, result(vote2).get("agree"));
		assertEquals(1, result(vote2).get("disagree"));

		ResponseEntity<Map> card = client.get().uri("/api/forecasts/" + id).retrieve().toEntity(Map.class);
		assertEquals("OPEN", result(card).get("status"));
		assertEquals(1, result(card).get("agree"));

		ResponseEntity<Map> withdrawn = client.post().uri("/api/forecasts/" + id + "/withdraw")
				.body(Map.of("reason", "근거 오류")).retrieve().toEntity(Map.class);
		assertEquals(200, withdrawn.getStatusCode().value());
		assertEquals(true, withdrawn.getBody().get("isSuccess"));

		ResponseEntity<Map> again = client.post().uri("/api/forecasts/" + id + "/withdraw")
				.body(Map.of("reason", "중복")).retrieve().toEntity(Map.class);
		assertEquals(409, again.getStatusCode().value());
		assertEquals("APP4091", again.getBody().get("code"));

		ResponseEntity<Map> lateVote = vote(client, id, 3, "AGREE");
		assertEquals(409, lateVote.getStatusCode().value());
		assertEquals("APP4090", lateVote.getBody().get("code"));

		ResponseEntity<Map> myVotes = client.get().uri("/api/me/votes")
				.header("X-User-Id", "1").retrieve().toEntity(Map.class);
		Map<String, Object> mine = (Map<String, Object>) ((List<?>) myVotes.getBody().get("result")).get(0);
		assertEquals("WITHDRAWN", mine.get("status"));
		assertEquals("근거 오류", mine.get("withdrawReason"));
		assertEquals("DISAGREE", mine.get("choice"));
	}

	@Test
	void 레디스_유실_후_재조정_잡이_DB_기준으로_복구한다() {
		RestClient client = client();

		ResponseEntity<Map> published = client.post().uri("/api/forecasts")
				.body(Map.of(
						"ticker", "371460",
						"direction", "DOWN",
						"endAt", Instant.now().plusSeconds(3600).toString(),
						"rationale", "재조정 테스트"))
				.retrieve().toEntity(Map.class);
		long id = ((Number) result(published).get("id")).longValue();
		vote(client, id, 1, "AGREE");
		vote(client, id, 2, "AGREE");
		vote(client, id, 3, "DISAGREE");

		redisTemplate.getConnectionFactory().getConnection().serverCommands().flushAll();
		ResponseEntity<Map> lost = client.get().uri("/api/forecasts/" + id).retrieve().toEntity(Map.class);
		assertEquals(0, result(lost).get("agree"));

		transactionTemplate.executeWithoutResult(status -> {
			redisRebuildRepository.record("forecast", String.valueOf(id));
			redisRebuildRepository.record("user", "1");
		});
		rebuildService.rebuildPending();

		ResponseEntity<Map> restored = client.get().uri("/api/forecasts/" + id).retrieve().toEntity(Map.class);
		assertEquals(2, result(restored).get("agree"));
		assertEquals(1, result(restored).get("disagree"));
		assertEquals("1", redisTemplate.opsForValue().get("forecast:" + id + ":open"));
		assertEquals(Boolean.TRUE,
				redisTemplate.opsForSet().isMember("user:1:voted", String.valueOf(id)));
		assertEquals(0, redisRebuildRepository.findTop100ByStatusOrderByRequestedAtAsc("PENDING").size());
	}

	@Test
	void 서킷_열림_저하_모드에서도_투표를_받고_복귀_후_재조정으로_수렴한다() {
		RestClient client = client();

		ResponseEntity<Map> published = client.post().uri("/api/forecasts")
				.body(Map.of(
						"ticker", "091160",
						"direction", "NEUTRAL",
						"endAt", Instant.now().plusSeconds(3600).toString(),
						"rationale", "저하 모드 테스트"))
				.retrieve().toEntity(Map.class);
		long id = ((Number) result(published).get("id")).longValue();
		vote(client, id, 1, "AGREE");

		redisCircuitBreaker.transitionToOpenState();
		try {
			ResponseEntity<Map> degraded = vote(client, id, 2, "AGREE");
			assertEquals(200, degraded.getStatusCode().value());
			assertEquals(2, result(degraded).get("agree"));

			ResponseEntity<Map> card = client.get().uri("/api/forecasts/" + id).retrieve().toEntity(Map.class);
			assertEquals(2, result(card).get("agree"));
		} finally {
			redisCircuitBreaker.transitionToClosedState();
		}

		assertEquals(1, redisTemplate.opsForSet().size("vote:" + id + ":agree"));
		rebuildService.rebuildPending();
		assertEquals(2, redisTemplate.opsForSet().size("vote:" + id + ":agree"));
		assertEquals(0, redisRebuildRepository.findTop100ByStatusOrderByRequestedAtAsc("PENDING").size());
	}

	private Map<String, Object> result(ResponseEntity<Map> response) {
		return (Map<String, Object>) response.getBody().get("result");
	}

	private ResponseEntity<Map> vote(RestClient client, long forecastId, long userId, String choice) {
		return client.put().uri("/api/forecasts/" + forecastId + "/vote")
				.header("X-User-Id", String.valueOf(userId))
				.body(Map.of("choice", choice))
				.retrieve().toEntity(Map.class);
	}
}
