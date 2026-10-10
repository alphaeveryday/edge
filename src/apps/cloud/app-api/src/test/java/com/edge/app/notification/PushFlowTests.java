package com.edge.app.notification;

import com.edge.app.ContainerTests;
import com.edge.app.notification.service.ExpoPushClient;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.util.List;
import java.util.Map;

import static com.edge.app.ApiCalls.call;
import static com.edge.app.ApiCalls.result;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.after;
import static org.mockito.Mockito.clearInvocations;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 답글 알림의 푸시가 글쓴이가 로그인한 기기에만 가는지
 * 로그아웃 뒤 게스트 재등록, 토큰 이동, 무효 토큰 정리
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class PushFlowTests extends ContainerTests {
    private static final String PUSH = "/api/v1/notifications/push-token";

    @LocalServerPort
    int port;
    @Autowired
    JdbcTemplate jdbc;
    @MockitoBean
    ExpoPushClient expo;

    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values ('069500','i1','XKRX','KODEX 200','kospi') on conflict (code) do nothing");
    }

    // 같은 DB 를 쓰는 다른 클래스의 전체 피드 단언 보호
    @AfterAll
    static void hidePosts(@Autowired JdbcTemplate jdbc) {
        jdbc.update("update post set deleted_at = now() where author_id in (select id from member where email like 'push%@example.com')");
    }

    String member(String email) {
        signupCode(email);
        Map<String, Object> auth = result(call(port, "POST", "/api/v1/auth/signup",
                Map.of("email", email, "password", "pw123456", "nick", "n", "code", SIGNUP_CODE)));
        String token = (String) auth.get("accessToken");
        // 글쓰기 조건인 관심 ETF
        result(call(port, "PUT", "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("069500")), "Authorization", "Bearer " + token));
        return token;
    }

    ResponseEntity<Map> register(String token, String bearer, String device) {
        return bearer == null
                ? call(port, "PUT", PUSH, Map.of("token", token), "X-Device-Id", device)
                : call(port, "PUT", PUSH, Map.of("token", token), "Authorization", "Bearer " + bearer, "X-Device-Id", device);
    }

    String post(String bearer) {
        return (String) ((Map<?, ?>) result(call(port, "POST", "/api/v1/posts", Map.of("body", "글", "tags", List.of("069500")),
                "Authorization", "Bearer " + bearer))).get("id");
    }

    void reply(String post, String bearer, String body) {
        result(call(port, "POST", "/api/v1/posts/" + post + "/replies", Map.of("body", body), "Authorization", "Bearer " + bearer));
    }

    @Test
    void replyPushReachesAuthorDeviceUntilGuestReRegistration() {
        when(expo.send(any(), any(), any(), any())).thenReturn(List.of());
        String author = member("push1@example.com");
        String other = member("push2@example.com");
        result(register("ExponentPushToken[p1]", author, "push-dev-1"));
        String post = post(author);

        reply(post, other, "답글 하나");
        verify(expo, timeout(3000)).send(List.of("ExponentPushToken[p1]"), "새 답글", "답글 하나", Map.of("postId", post));

        // 로그아웃 뒤 앱의 게스트 재등록은 회원 알림 대상에서 제외
        result(register("ExponentPushToken[p1]", null, "push-dev-1"));
        clearInvocations(expo);
        reply(post, other, "답글 둘");
        verify(expo, after(1000).never()).send(any(), any(), any(), any());
    }

    @Test
    void sameTokenMovesToLatestDeviceAndDeadTokenIsCleared() {
        String author = member("push3@example.com");
        String other = member("push4@example.com");
        result(register("ExponentPushToken[p3]", author, "push-dev-3"));
        result(register("ExponentPushToken[p3]", author, "push-dev-4"));
        assertNull(jdbc.queryForObject("select push_token from device where device_key = 'push-dev-3'", String.class));

        when(expo.send(any(), any(), any(), any())).thenReturn(List.of("ExponentPushToken[p3]"));
        String post = post(author);
        reply(post, other, "답글");
        verify(expo, timeout(3000)).send(List.of("ExponentPushToken[p3]"), "새 답글", "답글", Map.of("postId", post));
        // 발송 직후 정리까지의 비동기 간격
        String left = "ExponentPushToken[p3]";
        for (int i = 0; i < 30 && left != null; i++) {
            sleep();
            left = jdbc.queryForObject("select push_token from device where device_key = 'push-dev-4'", String.class);
        }
        assertNull(left, "Expo 가 무효라고 한 토큰 삭제");
    }

    private static void sleep() {
        try {
            Thread.sleep(100);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    @Test
    void rejectsMalformedTokenAndMissingDeviceHeader() {
        String author = member("push5@example.com");
        assertEquals(400, register("not-a-token", author, "push-dev-5").getStatusCode().value());
        assertEquals(400, call(port, "PUT", PUSH, Map.of("token", "ExponentPushToken[p5]"), "Authorization", "Bearer " + author)
                .getStatusCode().value());
    }
}
