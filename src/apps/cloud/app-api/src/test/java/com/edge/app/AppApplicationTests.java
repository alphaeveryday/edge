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
