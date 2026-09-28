package com.edge.app.common;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestClient;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 계약의 와이어 규약: 선택 필드는 키 생략, nextCursor 는 null 키 유지, size 는 1..100 밖이면 COMMON400. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ResponseShapeTests extends ContainerTests {
    @LocalServerPort
    int port;

    ResponseEntity<String> get(String uri) {
        return RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build()
                .get().uri(uri).header("X-Device-Id", "shape").retrieve().toEntity(String.class);
    }

    @Test
    void lastPageKeepsNextCursorNull() {
        String body = get("/api/v1/notifications").getBody();
        assertTrue(body.contains("\"nextCursor\":null"), body);
    }

    @Test
    void nullResultIsOmitted() {
        String body = get("/api/v1/notifications/read-all").getBody();
        // POST 가 아닌 GET 이라 405 이고 result 가 null 인 실패 봉투. 키 자체가 없어야 한다.
        assertFalse(body.contains("\"result\""), body);
    }

    @Test
    void sizeOutOfRangeIsBadRequest() {
        assertEquals(400, get("/api/v1/notifications?size=101").getStatusCode().value());
        assertEquals(400, get("/api/v1/notifications?size=0").getStatusCode().value());
        assertTrue(get("/api/v1/notifications?size=101").getBody().contains("COMMON400"));
        assertEquals(200, get("/api/v1/notifications?size=100").getStatusCode().value());
    }
}
