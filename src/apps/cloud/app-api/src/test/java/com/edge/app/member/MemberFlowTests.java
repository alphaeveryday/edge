package com.edge.app.member;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestClient;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class MemberFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @SuppressWarnings("unchecked")
    ResponseEntity<Map> call(String method, String uri, Object body, String bearer) {
        var spec = RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build().method(HttpMethod.valueOf(method)).uri(uri);
        if (bearer != null) {
            spec.header("Authorization", "Bearer " + bearer);
        }
        if (body != null) {
            spec.body(body);
        }
        return spec.retrieve().toEntity(Map.class);
    }

    @SuppressWarnings("unchecked")
    Map<String, Object> result(ResponseEntity<Map> res) {
        return (Map<String, Object>) res.getBody().get("result");
    }

    String signup(String email) {
        return (String) result(call("POST", "/api/v1/auth/signup", Map.of("email", email, "password", "pw", "nick", "n"), null)).get("accessToken");
    }

    @Test
    void updateNormalizesHandleAndRejectsTakenOne() {
        String a = signup("m1@example.com");
        String b = signup("m2@example.com");
        var me = result(call("PATCH", "/api/v1/me", Map.of("nick", "새닉", "handle", "orca_one"), a));
        assertEquals("새닉", me.get("nick"));
        assertEquals("@orca_one", me.get("handle"), "앱 표기 형식 @ 를 서버가 보장");
        var taken = call("PATCH", "/api/v1/me", Map.of("handle", "@orca_one"), b);
        assertEquals(400, taken.getStatusCode().value());
        // 자기 handle 그대로는 허용.
        assertEquals(200, call("PATCH", "/api/v1/me", Map.of("handle", "@orca_one"), a).getStatusCode().value());
        assertEquals(400, call("PATCH", "/api/v1/me", Map.of("nick", ""), a).getStatusCode().value());
    }

    @Test
    void disclaimerAcceptanceIsRecordedOnce() {
        String a = signup("m3@example.com");
        var me = result(call("POST", "/api/v1/me/disclaimer", null, a));
        assertNotNull(me.get("disclaimerAcceptedAt"));
        assertEquals(me.get("disclaimerAcceptedAt"), result(call("GET", "/api/v1/me", null, a)).get("disclaimerAcceptedAt"));
    }
}
