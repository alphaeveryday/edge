package com.edge.app.adapter.webapi.notification;

import com.edge.app.adapter.integration.notification.ExpoPushClient;
import com.edge.app.domain.member.MemberFixture;
import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MockMvcTester.MockMvcRequestBuilder;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;

import static com.edge.app.support.JsonResults.read;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.after;
import static org.mockito.Mockito.clearInvocations;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 댓글 알림의 푸시가 글쓴이가 로그인한 기기에만 가는지
 * 로그아웃 뒤 게스트 재등록, 토큰 이동, 무효 토큰 정리
 */
@WebApiAdapterTest
@MockitoBean(types = ExpoPushClient.class)
record PushApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper, JdbcTemplate jdbc, ExpoPushClient expo) {
    private static final String PUSH = "/api/v1/notifications/push-token";

    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values ('069500','i1','XKRX','KODEX 200','kospi') on conflict (code) do nothing");
    }

    @Test
    void commentPushReachesAuthorDeviceUntilGuestReRegistration() {
        when(expo.send(any(), any(), any(), any())).thenReturn(List.of());
        String author = member("push1@example.com");
        String other = member("push2@example.com");
        assertThat(register("ExponentPushToken[p1]", author, "push-dev-1")).hasStatus2xxSuccessful();
        String post = post(author);

        comment(post, other, "댓글 하나");
        verify(expo, timeout(3000)).send(List.of("ExponentPushToken[p1]"), "새 댓글", "댓글 하나", Map.of("postId", post));

        // 로그아웃 뒤 앱의 게스트 재등록은 회원 알림 대상에서 제외
        assertThat(register("ExponentPushToken[p1]", null, "push-dev-1")).hasStatus2xxSuccessful();
        clearInvocations(expo);
        comment(post, other, "댓글 둘");
        verify(expo, after(1000).never()).send(any(), any(), any(), any());
    }

    @Test
    void sameTokenMovesToLatestDeviceAndDeadTokenIsCleared() {
        String author = member("push3@example.com");
        String other = member("push4@example.com");
        assertThat(register("ExponentPushToken[p3]", author, "push-dev-3")).hasStatus2xxSuccessful();
        assertThat(register("ExponentPushToken[p3]", author, "push-dev-4")).hasStatus2xxSuccessful();
        assertThat(jdbc.queryForObject("select push_token from device where device_key = 'push-dev-3'", String.class)).isNull();

        when(expo.send(any(), any(), any(), any())).thenReturn(List.of("ExponentPushToken[p3]"));
        String post = post(author);
        comment(post, other, "댓글");
        verify(expo, timeout(3000)).send(List.of("ExponentPushToken[p3]"), "새 댓글", "댓글", Map.of("postId", post));
        // 발송 직후 정리까지의 비동기 간격
        String left = "ExponentPushToken[p3]";
        for (int i = 0; i < 30 && left != null; i++) {
            sleep();
            left = jdbc.queryForObject("select push_token from device where device_key = 'push-dev-4'", String.class);
        }
        assertThat(left).as("Expo 가 무효라고 한 토큰 삭제").isNull();
    }

    @Test
    void rejectsMalformedTokenAndMissingDeviceHeader() {
        String author = member("push5@example.com");
        assertThat(register("not-a-token", author, "push-dev-5")).hasStatus(400);
        assertThat(mvcTester.put().uri(PUSH).header("Authorization", "Bearer " + author)
                .contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(Map.of("token", "ExponentPushToken[p5]"))).exchange())
                .hasStatus(400);
    }

    private MvcTestResult call(HttpMethod method, String uri, Object body, String... headers) {
        MockMvcRequestBuilder request = mvcTester.method(method).uri(uri)
                .contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body));
        for (int i = 0; i < headers.length; i += 2) {
            request.header(headers[i], headers[i + 1]);
        }
        return request.exchange();
    }

    private String member(String email) {
        MemberFixture.plantSignupCode(jdbc, email);
        String token = read(call(HttpMethod.POST, "/api/v1/auth/signup",
                Map.of("email", email, "password", "pw123456", "nick", "n", "code", MemberFixture.SIGNUP_CODE)), "$.result.accessToken");
        // 글쓰기 조건인 관심 ETF
        assertThat(call(HttpMethod.PUT, "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("069500")), "Authorization", "Bearer " + token))
                .hasStatus2xxSuccessful();
        return token;
    }

    private MvcTestResult register(String token, String bearer, String device) {
        return bearer == null
                ? call(HttpMethod.PUT, PUSH, Map.of("token", token), "X-Device-Id", device)
                : call(HttpMethod.PUT, PUSH, Map.of("token", token), "Authorization", "Bearer " + bearer, "X-Device-Id", device);
    }

    private String post(String bearer) {
        return read(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", "글", "tags", List.of("069500")), "Authorization", "Bearer " + bearer), "$.result.id");
    }

    private void comment(String post, String bearer, String body) {
        assertThat(call(HttpMethod.POST, "/api/v1/posts/" + post + "/comments", Map.of("body", body), "Authorization", "Bearer " + bearer))
                .hasStatus2xxSuccessful();
    }

    private static void sleep() {
        try {
            Thread.sleep(100);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
