package com.edge.app.notification;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 답글이 글쓴이에게 comm 알림을 만들고, 읽음은 본인 것만, 목록은 종류 필터와 커서. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class NotificationFlowTests extends ContainerTests {
    @LocalServerPort
    int port;
    @Autowired
    JdbcTemplate jdbc;

    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values ('069500','i1','XKRX','KODEX 200','kospi') on conflict (code) do nothing");
    }

    @SuppressWarnings("unchecked")
    ResponseEntity<Map> call(String method, String uri, Object body, String auth) {
        var spec = RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build().method(HttpMethod.valueOf(method)).uri(uri);
        spec.header(auth.startsWith("dev:") ? "X-Device-Id" : "Authorization", auth.startsWith("dev:") ? auth.substring(4) : "Bearer " + auth);
        if (body != null) {
            spec.body(body);
        }
        return spec.retrieve().toEntity(Map.class);
    }

    @SuppressWarnings("unchecked")
    Map<String, Object> result(ResponseEntity<Map> res) {
        assertTrue(res.getStatusCode().is2xxSuccessful(), String.valueOf(res.getBody()));
        return (Map<String, Object>) res.getBody().get("result");
    }

    @SuppressWarnings("unchecked")
    List<Map<String, Object>> items(Map<String, Object> page) {
        return (List<Map<String, Object>>) page.get("items");
    }

    String member(String email) {
        String token = (String) result(call("POST", "/api/v1/auth/signup", Map.of("email", email, "password", "pw", "nick", "n"), "dev:none")).get("accessToken");
        result(call("PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("069500")), token));
        return token;
    }

    @Test
    void replyNotifiesPostAuthorExceptSelf() {
        String author = member("n1@example.com");
        String other = member("n2@example.com");
        String post = (String) result(call("POST", "/api/v1/posts", Map.of("body", "글", "tags", List.of("069500")), author)).get("id");
        result(call("POST", "/api/v1/posts/" + post + "/replies", Map.of("body", "내 답글"), author));
        result(call("POST", "/api/v1/posts/" + post + "/replies", Map.of("body", "남의 답글"), other));

        assertEquals(1, result(call("GET", "/api/v1/notifications/unread-count", null, author)).get("count"));
        assertEquals(0, result(call("GET", "/api/v1/notifications/unread-count", null, other)).get("count"));
        List<Map<String, Object>> list = items(result(call("GET", "/api/v1/notifications", null, author)));
        assertEquals(1, list.size());
        Map<String, Object> n = list.get(0);
        assertEquals("comm", n.get("kind"));
        assertEquals(post, n.get("postId"));
        assertEquals("남의 답글", n.get("body"));
        assertEquals(false, n.get("read"));
        assertFalse(n.containsKey("etf"));

        // 남의 알림은 읽음 처리되지 않는다.
        assertEquals(200, call("POST", "/api/v1/notifications/" + n.get("id") + "/read", null, other).getStatusCode().value());
        assertEquals(1, result(call("GET", "/api/v1/notifications/unread-count", null, author)).get("count"));
        assertEquals(200, call("POST", "/api/v1/notifications/" + n.get("id") + "/read", null, author).getStatusCode().value());
        assertEquals(0, result(call("GET", "/api/v1/notifications/unread-count", null, author)).get("count"));
        assertEquals(true, items(result(call("GET", "/api/v1/notifications", null, author))).get(0).get("read"));
        assertEquals(200, call("POST", "/api/v1/notifications/abc/read", null, author).getStatusCode().value());
    }

    @Test
    void kindFilterAndCursorAndReadAll() {
        long principal = jdbc.queryForObject("insert into device(device_key) values ('noti-dev') returning id", Long.class);
        principal = jdbc.queryForObject("insert into principal(kind, device_id) values ('device', ?) returning id", Long.class, principal);
        for (int i = 0; i < 3; i++) {
            jdbc.update("insert into notification(principal_id, kind, etf_code, title, body, created_at) values (?, 'watch', '069500', 't', ?, now() + make_interval(secs => ?))", principal, "s" + i, i);
        }
        jdbc.update("insert into notification(principal_id, kind, title, body, created_at) values (?, 'comm', 't', 'c', now() + interval '10 seconds')", principal);

        Map<String, Object> page = result(call("GET", "/api/v1/notifications?size=2", null, "dev:noti-dev"));
        assertEquals(List.of("c", "s2"), items(page).stream().map(i -> i.get("body")).toList(), "최신순");
        assertNotNull(page.get("nextCursor"));
        Map<String, Object> next = result(call("GET", "/api/v1/notifications?size=2&cursor=" + page.get("nextCursor"), null, "dev:noti-dev"));
        assertEquals(List.of("s1", "s0"), items(next).stream().map(i -> i.get("body")).toList());
        assertNull(next.get("nextCursor"));

        assertEquals(3, items(result(call("GET", "/api/v1/notifications?kind=watch", null, "dev:noti-dev"))).size());
        assertEquals(4, items(result(call("GET", "/api/v1/notifications?kind=all", null, "dev:noti-dev"))).size());
        assertEquals("069500", items(result(call("GET", "/api/v1/notifications?kind=watch", null, "dev:noti-dev"))).get(0).get("etf"));
        assertEquals(1, items(result(call("GET", "/api/v1/notifications?kind=comm", null, "dev:noti-dev"))).size());
        assertEquals(400, call("GET", "/api/v1/notifications?kind=signal", null, "dev:noti-dev").getStatusCode().value(), "디자인에 없는 종류");
        assertEquals(400, call("GET", "/api/v1/notifications?kind=spam", null, "dev:noti-dev").getStatusCode().value());

        assertEquals(4, result(call("GET", "/api/v1/notifications/unread-count", null, "dev:noti-dev")).get("count"));
        result(call("POST", "/api/v1/notifications/read-all", null, "dev:noti-dev"));
        assertEquals(0, result(call("GET", "/api/v1/notifications/unread-count", null, "dev:noti-dev")).get("count"));
        assertEquals(0, items(result(call("GET", "/api/v1/notifications", null, "dev:other-dev"))).size(), "디바이스 격리");
    }
}
