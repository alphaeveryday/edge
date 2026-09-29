package com.edge.app.community.post;

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

/** PRD 커뮤니티 정책(관심 ETF 글쓰기·태그 3개·회원 쓰기)과 커서 페이지·카운터. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class PostFlowTests extends ContainerTests {
    @LocalServerPort
    int port;

    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('069500','i1','XKRX','KODEX 200','kospi'), ('133690','i2','XKRX','TIGER 나스닥100','us'), "
                + "('305720','i3','XKRX','KODEX 2차전지','battery'), ('091160','i4','XKRX','KODEX 반도체','semi') on conflict (code) do nothing");
    }

    @SuppressWarnings("unchecked")
    ResponseEntity<Map> call(String method, String uri, Object body, String auth) {
        var spec = RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build().method(HttpMethod.valueOf(method)).uri(uri);
        if (auth != null) {
            spec.header(auth.startsWith("dev:") ? "X-Device-Id" : "Authorization", auth.startsWith("dev:") ? auth.substring(4) : "Bearer " + auth);
        }
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

    String member(String email, String nick, List<String> watched) {
        Map<String, Object> auth = result(call("POST", "/api/v1/auth/signup", Map.of("email", email, "password", "pw", "nick", nick), null));
        String token = (String) auth.get("accessToken");
        result(call("PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", watched), token));
        return token;
    }

    Map<String, Object> post(String token, String body, List<String> tags) {
        return result(call("POST", "/api/v1/posts", Map.of("body", body, "tags", tags), token));
    }

    @Test
    void writingRequiresWatchedEtfAndAtMostThreeTags() {
        String a = member("p1@example.com", "A", List.of("069500", "133690", "305720"));
        var unwatched = call("POST", "/api/v1/posts", Map.of("body", "b", "tags", List.of("091160")), a);
        assertEquals(400, unwatched.getStatusCode().value());
        assertEquals("POST4002", unwatched.getBody().get("code"));
        var tooMany = call("POST", "/api/v1/posts", Map.of("body", "b", "tags", List.of("069500", "133690", "305720", "091160")), a);
        assertEquals("POST4003", tooMany.getBody().get("code"));
        assertEquals("ETF4001", call("POST", "/api/v1/posts", Map.of("body", "b", "tags", List.of("000000")), a).getBody().get("code"));
        assertEquals(400, call("POST", "/api/v1/posts", Map.of("body", "b", "tags", List.of()), a).getStatusCode().value());
        assertEquals(401, call("POST", "/api/v1/posts", Map.of("body", "b", "tags", List.of("069500")), "dev:g1").getStatusCode().value());

        Map<String, Object> created = post(a, "  첫 글 ", List.of("133690", "069500"));
        assertEquals("첫 글", created.get("body"));
        assertEquals(true, created.get("mine"));
        assertEquals(false, created.get("liked"));
        Map<String, Object> etf = (Map<String, Object>) created.get("etf");
        assertEquals("133690", etf.get("code"), "대표 ETF 는 첫 태그");
        assertEquals("us", etf.get("theme"));
        assertEquals("TIGER 나스닥100", etf.get("short"));
        assertEquals("A", ((Map<?, ?>) created.get("author")).get("name"));
        assertFalse(created.containsKey("title"));
        assertFalse(created.containsKey("repostOf"));
    }

    @Test
    void feedScopesAndCursorPaging() {
        String a = member("p2@example.com", "A2", List.of("069500", "305720"));
        String b = member("p3@example.com", "B2", List.of("133690"));
        String p1 = (String) post(a, "one", List.of("069500")).get("id");
        String p2 = (String) post(a, "two", List.of("305720")).get("id");
        String p3 = (String) post(b, "three", List.of("133690")).get("id");

        Map<String, Object> page = result(call("GET", "/api/v1/posts?size=2", null, "dev:g2"));
        assertEquals(List.of(p3, p2), items(page).stream().map(i -> i.get("id")).toList(), "최신순");
        assertNotNull(page.get("nextCursor"));
        Map<String, Object> next = result(call("GET", "/api/v1/posts?size=2&cursor=" + page.get("nextCursor"), null, "dev:g2"));
        assertEquals(List.of(p1), items(next).stream().map(i -> i.get("id")).toList());
        assertNull(next.get("nextCursor"));

        Map<String, Object> byCode = result(call("GET", "/api/v1/posts?code=305720", null, "dev:g2"));
        assertEquals(List.of(p2), items(byCode).stream().map(i -> i.get("id")).toList());

        // mine 은 요청자(회원 또는 게스트 디바이스)의 관심 ETF 글
        Map<String, Object> mine = result(call("GET", "/api/v1/posts?scope=mine", null, b));
        assertEquals(List.of(p3), items(mine).stream().map(i -> i.get("id")).toList());
        call("PUT", "/api/v1/etfs/305720/watch-groups", Map.of("groups", List.of("base")), "dev:pf-mine");
        List<Object> guestMine = items(result(call("GET", "/api/v1/posts?scope=mine", null, "dev:pf-mine"))).stream().map(i -> i.get("id")).toList();
        assertTrue(guestMine.contains(p2) && !guestMine.contains(p1) && !guestMine.contains(p3), "게스트 관심 ETF 글만");
        assertEquals(List.of(), items(result(call("GET", "/api/v1/posts?scope=mine", null, "dev:pf-none"))), "관심 없는 게스트는 빈 목록");
        assertEquals(400, call("GET", "/api/v1/posts?scope=random", null, "dev:g2").getStatusCode().value());
        assertEquals(400, call("GET", "/api/v1/posts?cursor=not-a-cursor", null, "dev:g2").getStatusCode().value());
    }

    @Test
    void likesAreIdempotentAndHotOrdersByLikes() {
        String a = member("p4@example.com", "A4", List.of("069500"));
        String b = member("p5@example.com", "B4", List.of("069500"));
        String cold = (String) post(a, "cold", List.of("069500")).get("id");
        String hot = (String) post(a, "hot", List.of("069500")).get("id");
        Map<String, Object> liked = result(call("PUT", "/api/v1/posts/" + hot + "/like", null, b));
        assertEquals(1, liked.get("like"));
        assertEquals(true, liked.get("liked"));
        assertEquals(1, result(call("PUT", "/api/v1/posts/" + hot + "/like", null, b)).get("like"), "멱등");
        assertEquals(2, result(call("PUT", "/api/v1/posts/" + hot + "/like", null, a)).get("like"));
        Map<String, Object> unliked = result(call("DELETE", "/api/v1/posts/" + hot + "/like", null, b));
        assertEquals(1, unliked.get("like"));
        assertEquals(false, unliked.get("liked"));
        assertEquals(1, result(call("DELETE", "/api/v1/posts/" + hot + "/like", null, b)).get("like"));

        List<Map<String, Object>> top = items(result(call("GET", "/api/v1/posts?scope=hot&size=1", null, "dev:g3")));
        assertEquals(hot, top.get(0).get("id"));
        assertEquals("POST4001", call("PUT", "/api/v1/posts/999999/like", null, b).getBody().get("code"));
        assertEquals(cold, result(call("GET", "/api/v1/posts/" + cold, null, null)).get("id"));
    }

    @Test
    void getCountsViewsAndFillsRequesterFieldsOnlyWithAuth() {
        String a = member("p6@example.com", "A6", List.of("069500"));
        String id = (String) post(a, "view me", List.of("069500")).get("id");
        Map<String, Object> anon = result(call("GET", "/api/v1/posts/" + id, null, null));
        assertEquals(1, anon.get("views"));
        assertEquals(false, anon.get("mine"));
        Map<String, Object> asAuthor = result(call("GET", "/api/v1/posts/" + id, null, a));
        assertEquals(2, asAuthor.get("views"));
        assertEquals(true, asAuthor.get("mine"));
        assertEquals("POST4001", call("GET", "/api/v1/posts/abc", null, null).getBody().get("code"));
    }

    @Test
    void repliesPageOldestFirstAndRemovalIsAuthorOnly() {
        String a = member("p7@example.com", "A7", List.of("069500"));
        String b = member("p8@example.com", "B7", List.of("069500"));
        String id = (String) post(a, "thread", List.of("069500")).get("id");
        for (String body : List.of("r1", "r2", "r3")) {
            Map<String, Object> reply = result(call("POST", "/api/v1/posts/" + id + "/replies", Map.of("body", body), b));
            assertEquals("B7", ((Map<?, ?>) reply.get("author")).get("name"));
        }
        assertEquals(3, result(call("GET", "/api/v1/posts/" + id, null, null)).get("reply"));
        Map<String, Object> page = result(call("GET", "/api/v1/posts/" + id + "/replies?size=2", null, null));
        assertEquals(List.of("r1", "r2"), items(page).stream().map(i -> i.get("body")).toList());
        Map<String, Object> next = result(call("GET", "/api/v1/posts/" + id + "/replies?size=2&cursor=" + page.get("nextCursor"), null, null));
        assertEquals(List.of("r3"), items(next).stream().map(i -> i.get("body")).toList());
        assertNull(next.get("nextCursor"));

        assertEquals(403, call("DELETE", "/api/v1/posts/" + id, null, b).getStatusCode().value());
        assertEquals(200, call("DELETE", "/api/v1/posts/" + id, null, a).getStatusCode().value());
        assertEquals(404, call("GET", "/api/v1/posts/" + id, null, null).getStatusCode().value());
        assertEquals(404, call("POST", "/api/v1/posts/" + id + "/replies", Map.of("body", "late"), b).getStatusCode().value());
        List<Map<String, Object>> feed = items(result(call("GET", "/api/v1/posts?code=069500", null, "dev:g4")));
        assertTrue(feed.stream().noneMatch(i -> id.equals(i.get("id"))));
    }

    @Test
    void deletedAuthorIsMasked() {
        String a = member("p9@example.com", "A9", List.of("069500"));
        String id = (String) post(a, "bye", List.of("069500")).get("id");
        result(call("DELETE", "/api/v1/me", null, a));
        Map<String, Object> post = result(call("GET", "/api/v1/posts/" + id, null, null));
        assertEquals("탈퇴한 사용자", ((Map<?, ?>) post.get("author")).get("name"));
    }
}
