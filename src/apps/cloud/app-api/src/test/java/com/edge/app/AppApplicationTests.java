package com.edge.app;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.http.ResponseEntity;
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

	private RestClient client() {
		return RestClient.builder()
				.baseUrl("http://localhost:" + port)
				.defaultStatusHandler(code -> true, (request, response) -> {
				})
				.build();
	}

	@Test
	void 투표_연타_철회_흐름() {
		RestClient client = client();
		long id = publish(client);

		ResponseEntity<Map> vote1 = vote(client, id, 1, "AGREE");
		assertEquals(200, vote1.getStatusCode().value());
		assertEquals(1, result(vote1).get("agree"));

		// 락 TTL(500ms) 내 연타 — 반영 없이 현재 집계만 돌아온다
		ResponseEntity<Map> rapid = vote(client, id, 1, "DISAGREE");
		assertEquals(200, rapid.getStatusCode().value());
		assertEquals(1, result(rapid).get("agree"));
		assertEquals(0, result(rapid).get("disagree"));

		sleep(600); // 락 만료 후 정상 변경
		ResponseEntity<Map> changed = vote(client, id, 1, "DISAGREE");
		assertEquals(0, result(changed).get("agree"));
		assertEquals(1, result(changed).get("disagree"));

		vote(client, id, 2, "AGREE");

		ResponseEntity<Map> card = client.get().uri("/api/forecasts/" + id).retrieve().toEntity(Map.class);
		assertEquals("OPEN", result(card).get("status"));
		assertEquals(1, result(card).get("agree"));

		// 백업은 동기 — 즉시 DB 에서 조회된다
		Map<String, Object> mine = myVotes(client, 1).stream()
				.filter(v -> ((Number) v.get("forecastId")).longValue() == id)
				.findFirst().orElseThrow();
		assertEquals("DISAGREE", mine.get("choice"));

		assertEquals(200, withdraw(client, id, "근거 오류"));
		assertEquals(409, withdraw(client, id, "중복"));

		ResponseEntity<Map> late = vote(client, id, 3, "AGREE");
		assertEquals(409, late.getStatusCode().value());
		assertEquals("APP4090", late.getBody().get("code"));

		assertEquals("WITHDRAWN", myVotes(client, 1).stream()
				.filter(v -> ((Number) v.get("forecastId")).longValue() == id)
				.findFirst().orElseThrow().get("status"));
	}

	private long publish(RestClient client) {
		ResponseEntity<Map> published = client.post().uri("/api/forecasts")
				.body(Map.of(
						"ticker", "069500",
						"direction", "UP",
						"endAt", Instant.now().plusSeconds(3600).toString(),
						"rationale", "테스트"))
				.retrieve().toEntity(Map.class);
		assertEquals(200, published.getStatusCode().value());
		return ((Number) result(published).get("id")).longValue();
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

	private void sleep(long millis) {
		try {
			Thread.sleep(millis);
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
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
