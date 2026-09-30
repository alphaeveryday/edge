package com.edge.app.community.report;

import com.edge.app.ContainerTests;
import com.edge.app.common.mail.Mailer;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/** 스토어 심사 요건(악성 사용자 차단·신고 접수와 운영자 대응)의 서버 쪽 보장. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ModerationFlowTests extends ContainerTests {
    @LocalServerPort
    int port;
    @MockitoBean
    Mailer mailer;

    record Member(String token, String handle) {
    }

    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('069500','i1','XKRX','KODEX 200','kospi') on conflict (code) do nothing");
    }

    @SuppressWarnings("unchecked")
    ResponseEntity<Map> call(String method, String uri, Object body, String token) {
        var spec = RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build().method(HttpMethod.valueOf(method)).uri(uri);
        if (token != null) {
            spec.header(token.startsWith("dev:") ? "X-Device-Id" : "Authorization", token.startsWith("dev:") ? token.substring(4) : "Bearer " + token);
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
    List<String> ids(ResponseEntity<Map> res) {
        return ((List<Map<String, Object>>) result(res).get("items")).stream().map(m -> (String) m.get("id")).toList();
    }

    @SuppressWarnings("unchecked")
    Member member(String email) {
        Map<String, Object> auth = result(call("POST", "/api/v1/auth/signup", Map.of("email", email, "password", "pw123456", "nick", "회원"), null));
        String token = (String) auth.get("accessToken");
        result(call("PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("069500")), token));
        return new Member(token, (String) ((Map<String, Object>) auth.get("me")).get("handle"));
    }

    String post(Member m, String body) {
        return (String) result(call("POST", "/api/v1/posts", Map.of("body", body, "tags", List.of("069500")), m.token())).get("id");
    }

    String reply(Member m, String postId, String body) {
        return (String) result(call("POST", "/api/v1/posts/" + postId + "/replies", Map.of("body", body), m.token())).get("id");
    }

    int unread(Member m) {
        return ((Number) result(call("GET", "/api/v1/notifications/unread-count", null, m.token())).get("count")).intValue();
    }

    // 차단은 차단한 사람의 화면에서만 작동해야 하고, 다른 회원·게스트의 커뮤니티는 그대로여야 한다
    @Test
    void blockHidesAuthorOnlyForBlocker() {
        Member a = member("mb-a@example.com");
        Member b = member("mb-b@example.com");
        Member c = member("mb-c@example.com");
        String bPost = post(b, "차단될 글");
        String aPost = post(a, "A 의 글");

        assertEquals(200, call("PUT", "/api/v1/blocks/" + b.handle(), null, a.token()).getStatusCode().value());
        assertEquals(200, call("PUT", "/api/v1/blocks/" + b.handle(), null, a.token()).getStatusCode().value());

        for (String q : List.of("scope=all", "scope=hot", "scope=mine", "code=069500")) {
            assertFalse(ids(call("GET", "/api/v1/posts?size=100&" + q, null, a.token())).contains(bPost), q);
            assertTrue(ids(call("GET", "/api/v1/posts?size=100&" + q, null, c.token())).contains(bPost), q);
        }
        assertTrue(ids(call("GET", "/api/v1/posts?size=100", null, "dev:mb-guest")).contains(bPost));

        assertEquals(true, result(call("GET", "/api/v1/posts/" + bPost, null, a.token())).get("blocked"));
        assertNull(result(call("GET", "/api/v1/posts/" + bPost, null, c.token())).get("blocked"));

        int before = unread(a);
        String bReply = reply(b, aPost, "차단된 답글");
        String cReply = reply(c, aPost, "보이는 답글");
        assertEquals(before + 1, unread(a), "차단한 회원의 답글은 알림을 만들지 않는다");
        assertEquals(List.of(cReply), ids(call("GET", "/api/v1/posts/" + aPost + "/replies", null, a.token())));
        assertEquals(List.of(bReply, cReply), ids(call("GET", "/api/v1/posts/" + aPost + "/replies", null, c.token())));
    }

    @Test
    void blockRejectsSelfUnknownAndGuest() {
        Member a = member("mb-d@example.com");
        var self = call("PUT", "/api/v1/blocks/" + a.handle(), null, a.token());
        assertEquals(400, self.getStatusCode().value());
        assertEquals("MEMBER4004", self.getBody().get("code"));
        assertEquals("MEMBER4003", call("PUT", "/api/v1/blocks/@nobody00", null, a.token()).getBody().get("code"));
        assertEquals(401, call("PUT", "/api/v1/blocks/" + a.handle(), null, "dev:mb-g2").getStatusCode().value());
    }

    // 운영자가 24시간 안에 대응하려면 신고마다 원문이 담긴 메일이 한 번 가야 하고, 반복 신고가 메일 폭탄이 되면 안 된다
    @Test
    void reportMailsOperatorOncePerTarget() {
        Member author = member("mr-a@example.com");
        Member reporter = member("mr-b@example.com");
        String postId = post(author, "신고될 원문");
        String replyId = reply(author, postId, "신고될 답글");

        Map<String, String> body = Map.of("targetType", "post", "targetId", postId, "reason", "abuse");
        assertEquals(200, call("POST", "/api/v1/reports", body, reporter.token()).getStatusCode().value());
        assertEquals(200, call("POST", "/api/v1/reports", body, reporter.token()).getStatusCode().value());
        ArgumentCaptor<String> text = ArgumentCaptor.forClass(String.class);
        verify(mailer, times(1)).send(eq("operator@localhost"), anyString(), text.capture());
        assertTrue(text.getValue().contains("신고될 원문") && text.getValue().contains(author.handle()));

        assertEquals(200, call("POST", "/api/v1/reports", Map.of("targetType", "reply", "targetId", replyId, "reason", "spam"), reporter.token()).getStatusCode().value());
        verify(mailer, times(2)).send(eq("operator@localhost"), anyString(), anyString());

        assertEquals("POST4001", call("POST", "/api/v1/reports", Map.of("targetType", "post", "targetId", "999999999", "reason", "spam"), reporter.token()).getBody().get("code"));
        assertEquals(400, call("POST", "/api/v1/reports", Map.of("targetType", "post", "targetId", postId, "reason", "boring"), reporter.token()).getStatusCode().value());
        assertEquals(401, call("POST", "/api/v1/reports", body, "dev:mr-guest").getStatusCode().value());
    }
}
