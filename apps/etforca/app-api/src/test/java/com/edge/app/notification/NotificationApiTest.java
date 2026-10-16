package com.edge.app.notification;

import com.edge.app.member.MemberFixture;
import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MockMvcTester.MockMvcRequestBuilder;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.read;
import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * 댓글로 생기는 글쓴이의 comm 알림
 * 본인 알림에 한정한 읽음 처리와 종류 필터와 커서의 목록
 */
@WebApiAdapterTest
record NotificationApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper, JdbcTemplate jdbc) {
    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values ('069500','i1','XKRX','KODEX 200','kospi') on conflict (code) do nothing");
    }

    @Test
    void commentNotifiesPostAuthorExceptSelf() {
        String author = member("n1@example.com");
        String other = member("n2@example.com");
        String post = read(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", "글", "tags", List.of("069500")), author), "$.result.id");
        assertThat(call(HttpMethod.POST, "/api/v1/posts/" + post + "/comments", Map.of("body", "내 댓글"), author)).hasStatus2xxSuccessful();
        assertThat(call(HttpMethod.POST, "/api/v1/posts/" + post + "/comments", Map.of("body", "남의 댓글"), other)).hasStatus2xxSuccessful();

        assertThat(unread(author)).isEqualTo(1);
        assertThat(unread(other)).isEqualTo(0);
        List<Map<String, Object>> list = items(result(call(HttpMethod.GET, "/api/v1/notifications", null, author)));
        assertThat(list).hasSize(1);
        Map<String, Object> n = list.get(0);
        assertThat(n.get("kind")).isEqualTo("comm");
        assertThat(n.get("postId")).isEqualTo(post);
        assertThat(n.get("body")).isEqualTo("남의 댓글");
        assertThat(n.get("read")).isEqualTo(false);
        assertThat(n).doesNotContainKey("etf");

        // 남의 알림의 읽음 처리 불가
        assertThat(call(HttpMethod.POST, "/api/v1/notifications/" + n.get("id") + "/read", null, other)).hasStatusOk();
        assertThat(unread(author)).isEqualTo(1);
        assertThat(call(HttpMethod.POST, "/api/v1/notifications/" + n.get("id") + "/read", null, author)).hasStatusOk();
        assertThat(unread(author)).isEqualTo(0);
        assertThat(items(result(call(HttpMethod.GET, "/api/v1/notifications", null, author))).get(0).get("read")).isEqualTo(true);
        assertThat(call(HttpMethod.POST, "/api/v1/notifications/abc/read", null, author)).hasStatusOk();
    }

    @Test
    void commentRemovalRemovesItsNotificationOnly() {
        String author = member("n5@example.com");
        String other = member("n6@example.com");
        String post = read(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", "글", "tags", List.of("069500")), author), "$.result.id");
        String gone = read(call(HttpMethod.POST, "/api/v1/posts/" + post + "/comments", Map.of("body", "지울 댓글"), other), "$.result.id");
        assertThat(call(HttpMethod.POST, "/api/v1/posts/" + post + "/comments", Map.of("body", "남길 댓글"), other)).hasStatus2xxSuccessful();
        assertThat(unread(author)).isEqualTo(2);

        assertThat(call(HttpMethod.DELETE, "/api/v1/posts/" + post + "/comments/" + gone, null, other)).hasStatus2xxSuccessful();
        assertThat(unread(author)).isEqualTo(1);
        assertThat(bodies(result(call(HttpMethod.GET, "/api/v1/notifications", null, author)))).isEqualTo(List.of("남길 댓글"));
    }

    @Test
    void kindFilterAndCursorAndReadAll() {
        long principal = jdbc.queryForObject("insert into device(device_key) values ('noti-dev') returning id", Long.class);
        principal = jdbc.queryForObject("insert into principal(kind, device_id) values ('device', ?) returning id", Long.class, principal);
        for (int i = 0; i < 3; i++) {
            jdbc.update("insert into notification(principal_id, kind, etf_code, title, body, created_at) values (?, 'watch', '069500', 't', ?, now() + make_interval(secs => ?))", principal, "s" + i, i);
        }
        jdbc.update("insert into notification(principal_id, kind, title, body, created_at) values (?, 'comm', 't', 'c', now() + interval '10 seconds')", principal);

        Map<String, Object> page = result(call(HttpMethod.GET, "/api/v1/notifications?size=2", null, "dev:noti-dev"));
        assertThat(bodies(page)).as("최신순").isEqualTo(List.of("c", "s2"));
        assertThat(page.get("nextCursor")).isNotNull();
        Map<String, Object> next = result(call(HttpMethod.GET, "/api/v1/notifications?size=2&cursor=" + page.get("nextCursor"), null, "dev:noti-dev"));
        assertThat(bodies(next)).isEqualTo(List.of("s1", "s0"));
        assertThat(next.get("nextCursor")).isNull();

        assertThat(items(result(call(HttpMethod.GET, "/api/v1/notifications?kind=watch", null, "dev:noti-dev")))).hasSize(3);
        assertThat(items(result(call(HttpMethod.GET, "/api/v1/notifications?kind=all", null, "dev:noti-dev")))).hasSize(4);
        assertThat(items(result(call(HttpMethod.GET, "/api/v1/notifications?kind=watch", null, "dev:noti-dev"))).get(0).get("etf")).isEqualTo("069500");
        assertThat(items(result(call(HttpMethod.GET, "/api/v1/notifications?kind=comm", null, "dev:noti-dev")))).hasSize(1);
        assertThat(call(HttpMethod.GET, "/api/v1/notifications?kind=signal", null, "dev:noti-dev")).as("디자인에 없는 종류").hasStatus(400);
        assertThat(call(HttpMethod.GET, "/api/v1/notifications?kind=spam", null, "dev:noti-dev")).hasStatus(400);

        assertThat(unread("dev:noti-dev")).isEqualTo(4);
        assertThat(call(HttpMethod.POST, "/api/v1/notifications/read-all", null, "dev:noti-dev")).hasStatus2xxSuccessful();
        assertThat(unread("dev:noti-dev")).isEqualTo(0);
        assertThat(items(result(call(HttpMethod.GET, "/api/v1/notifications", null, "dev:other-dev")))).as("디바이스 격리").isEmpty();
    }

    private MvcTestResult call(HttpMethod method, String uri, Object body, String auth) {
        MockMvcRequestBuilder request = mvcTester.method(method).uri(uri)
                .header(auth.startsWith("dev:") ? "X-Device-Id" : "Authorization", auth.startsWith("dev:") ? auth.substring(4) : "Bearer " + auth);
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body));
        }
        return request.exchange();
    }

    private String member(String email) {
        MemberFixture.plantSignupCode(jdbc, email);
        String token = read(call(HttpMethod.POST, "/api/v1/auth/signup",
                Map.of("email", email, "password", "pw123456", "nick", "n", "code", MemberFixture.SIGNUP_CODE), "dev:none"), "$.result.accessToken");
        assertThat(call(HttpMethod.PUT, "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("069500")), token)).hasStatus2xxSuccessful();
        return token;
    }

    private Object unread(String auth) {
        return read(call(HttpMethod.GET, "/api/v1/notifications/unread-count", null, auth), "$.result.count");
    }

    private static List<Map<String, Object>> items(Map<String, Object> page) {
        return (List<Map<String, Object>>) page.get("items");
    }

    private static List<Object> bodies(Map<String, Object> page) {
        return items(page).stream().map(i -> i.get("body")).toList();
    }
}
