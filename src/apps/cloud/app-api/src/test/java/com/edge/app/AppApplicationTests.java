package com.edge.app;

import com.edge.app.service.VoteBackUpProcessor;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestClient;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

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
	VoteBackUpProcessor voteBackUpProcessor;

	@Autowired
	StringRedisTemplate redisTemplate;

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
	void 정상_경로_연타_뒤집기와_flush_반영() {
		RestClient client = client();
		long id = publish(client, "069500", "UP");

		ResponseEntity<Map> vote1 = vote(client, id, 1, "AGREE");
		assertEquals(200, vote1.getStatusCode().value());
		assertEquals(1, counts(vote1).get("agree"));

		// 마커·락 없음 — 연타 즉시 뒤집기도 Redis 도착 순서(seq)대로 정확히 토글된다
		ResponseEntity<Map> rapid = vote(client, id, 1, "DISAGREE");
		assertEquals(200, rapid.getStatusCode().value());
		assertEquals(0, counts(rapid).get("agree"));
		assertEquals(1, counts(rapid).get("disagree"));

		vote(client, id, 2, "AGREE");

		ResponseEntity<Map> card = client.get().uri("/api/forecasts/" + id).retrieve().toEntity(Map.class);
		assertEquals("OPEN", result(card).get("status"));
		assertEquals(1, result(card).get("agree"));
		assertEquals(1, result(card).get("disagree"));

		flushUntil(() -> myVotes(client, 1).stream()
				.anyMatch(v -> ((Number) v.get("forecastId")).longValue() == id
						&& "DISAGREE".equals(v.get("choice"))));

		assertEquals(200, withdraw(client, id, "근거 오류"));
		assertEquals(409, withdraw(client, id, "중복"));

		ResponseEntity<Map> late = vote(client, id, 3, "AGREE");
		assertEquals(409, late.getStatusCode().value());
		assertEquals("APP4090", late.getBody().get("code"));

		assertEquals("WITHDRAWN", myVotes(client, 1).stream()
				.filter(v -> ((Number) v.get("forecastId")).longValue() == id)
				.findFirst().orElseThrow().get("status"));
	}

	@Test
	void 우회_모드_투표와_복귀_재조정() {
		RestClient client = client();
		long id = publish(client, "371460", "DOWN");

		vote(client, id, 1, "AGREE");

		redisCircuitBreaker.transitionToOpenState();
		try {
			ResponseEntity<Map> bypass = vote(client, id, 2, "AGREE");
			assertEquals(200, bypass.getStatusCode().value());
			// 우회 응답은 DB COUNT — 미flush 분(user 1)이 빠져 과소 표시될 수 있다 (§8)
			assertTrue((int) counts(bypass).get("agree") >= 1);
		} finally {
			redisCircuitBreaker.transitionToClosedState(); // CLOSED 전이가 복귀 재조정을 트리거
		}

		ResponseEntity<Map> card = client.get().uri("/api/forecasts/" + id).retrieve().toEntity(Map.class);
		assertEquals(2, result(card).get("agree"));

		flushUntil(() -> myVotes(client, 1).stream()
				.anyMatch(v -> ((Number) v.get("forecastId")).longValue() == id));
		assertEquals(2L, redisTemplate.opsForHash().size("vote:{" + id + "}"), "재조정 후 두 표 모두 Redis에 존재");
	}

	private long publish(RestClient client, String ticker, String direction) {
		ResponseEntity<Map> published = client.post().uri("/api/forecasts")
				.body(Map.of(
						"ticker", ticker,
						"direction", direction,
						"endAt", Instant.now().plusSeconds(3600).toString(),
						"rationale", "테스트"))
				.retrieve().toEntity(Map.class);
		assertEquals(200, published.getStatusCode().value());
		return ((Number) result(published).get("id")).longValue();
	}

	private void flushUntil(java.util.function.BooleanSupplier condition) {
		for (int i = 0; i < 20 && !condition.getAsBoolean(); i++) {
			voteBackUpProcessor.flush();
			try {
				Thread.sleep(300);
			} catch (InterruptedException e) {
				Thread.currentThread().interrupt();
			}
		}
		assertTrue(condition.getAsBoolean());
	}

	private List<Map<String, Object>> myVotes(RestClient client, long userId) {
		ResponseEntity<Map> response = client.get().uri("/api/me/votes")
				.header("X-User-Id", String.valueOf(userId)).retrieve().toEntity(Map.class);
		return (List<Map<String, Object>>) response.getBody().get("result");
	}

	private int withdraw(RestClient client, long id, String reason) {
		return client.post().uri("/api/forecasts/" + id + "/withdraw")
				.body(Map.of("reason", reason)).retrieve().toEntity(Map.class)
				.getStatusCode().value();
	}

	private Map<String, Object> counts(ResponseEntity<Map> response) {
		return result(response);
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
