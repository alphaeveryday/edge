package com.edge.app.adapter.webapi.report;

import com.edge.app.adapter.integration.mail.Mailer;
import com.edge.app.domain.member.MemberFixture;
import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
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
import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/** 악성 사용자 차단과 신고 대응이라는 스토어 심사 요건의 서버 쪽 보장 */
@WebApiAdapterTest
@MockitoBean(types = Mailer.class)
record ModerationApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper, JdbcTemplate jdbc, Mailer mailer) {
    record Member(String token, String handle) {
    }

    @BeforeAll
    static void seedEtfs(@Autowired JdbcTemplate jdbc) {
        jdbc.update("insert into etf(code, instrument_id, market_code, name, theme_key) values "
                + "('069500','i1','XKRX','KODEX 200','kospi') on conflict (code) do nothing");
    }

    // 차단한 사람의 화면에 한정한 차단 효과
    // 다른 회원과 게스트 커뮤니티의 무영향
    @Test
    void blockHidesAuthorOnlyForBlocker() {
        Member a = member("mb-a@example.com");
        Member b = member("mb-b@example.com");
        Member c = member("mb-c@example.com");
        String bPost = post(b, "차단될 글");
        String aPost = post(a, "A 의 글");

        assertThat(call(HttpMethod.PUT, "/api/v1/blocks/" + b.handle(), null, a.token())).hasStatusOk();
        assertThat(call(HttpMethod.PUT, "/api/v1/blocks/" + b.handle(), null, a.token())).hasStatusOk();

        for (String q : List.of("scope=all", "scope=hot", "scope=mine", "code=069500")) {
            assertThat(ids(call(HttpMethod.GET, "/api/v1/posts?size=100&" + q, null, a.token()))).as(q).doesNotContain(bPost);
            assertThat(ids(call(HttpMethod.GET, "/api/v1/posts?size=100&" + q, null, c.token()))).as(q).contains(bPost);
        }
        assertThat(ids(call(HttpMethod.GET, "/api/v1/posts?size=100", null, "dev:mb-guest"))).contains(bPost);

        assertThat(call(HttpMethod.GET, "/api/v1/posts/" + bPost, null, a.token()))
                .hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.blocked").isEqualTo(true);
        Map<String, Object> seenByOther = result(call(HttpMethod.GET, "/api/v1/posts/" + bPost, null, c.token()));
        assertThat(seenByOther.get("blocked")).isNull();

        int before = unread(a);
        String bComment = comment(b, aPost, "차단된 댓글");
        String cComment = comment(c, aPost, "보이는 댓글");
        assertThat(unread(a)).as("차단한 회원의 댓글은 알림을 만들지 않는다").isEqualTo(before + 1);
        assertThat(ids(call(HttpMethod.GET, "/api/v1/posts/" + aPost + "/comments", null, a.token()))).isEqualTo(List.of(cComment));
        assertThat(ids(call(HttpMethod.GET, "/api/v1/posts/" + aPost + "/comments", null, c.token()))).isEqualTo(List.of(bComment, cComment));
    }

    @Test
    void blockRejectsSelfUnknownAndGuest() {
        Member a = member("mb-d@example.com");
        assertThat(call(HttpMethod.PUT, "/api/v1/blocks/" + a.handle(), null, a.token()))
                .hasStatus(400)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("MEMBER4004");
        assertThat(call(HttpMethod.PUT, "/api/v1/blocks/@nobody00", null, a.token())).bodyJson().extractingPath("$.code").isEqualTo("MEMBER4003");
        assertThat(call(HttpMethod.PUT, "/api/v1/blocks/" + a.handle(), null, "dev:mb-g2")).hasStatus(401);
    }

    // 운영자의 24시간 내 대응을 위한 신고별 원문 메일 1회 발송
    // 반복 신고의 메일 폭탄 방지
    @Test
    void reportMailsOperatorOncePerTarget() {
        Member author = member("mr-a@example.com");
        Member reporter = member("mr-b@example.com");
        String postId = post(author, "신고될 원문");
        String commentId = comment(author, postId, "신고될 댓글");

        Map<String, String> body = Map.of("targetType", "post", "targetId", postId, "reason", "abuse");
        assertThat(call(HttpMethod.POST, "/api/v1/reports", body, reporter.token())).hasStatusOk();
        assertThat(call(HttpMethod.POST, "/api/v1/reports", body, reporter.token())).hasStatusOk();
        ArgumentCaptor<String> text = ArgumentCaptor.forClass(String.class);
        verify(mailer, times(1)).send(eq("operator@localhost"), anyString(), text.capture());
        assertThat(text.getValue().contains("신고될 원문") && text.getValue().contains(author.handle())).isTrue();

        assertThat(call(HttpMethod.POST, "/api/v1/reports", Map.of("targetType", "comment", "targetId", commentId, "reason", "spam"), reporter.token()))
                .hasStatusOk();
        verify(mailer, times(2)).send(eq("operator@localhost"), anyString(), anyString());
        // 1.1.0 이전 앱의 reply 는 같은 comment 신고로 중복 처리
        assertThat(call(HttpMethod.POST, "/api/v1/reports", Map.of("targetType", "reply", "targetId", commentId, "reason", "spam"), reporter.token()))
                .hasStatusOk();
        verify(mailer, times(2)).send(eq("operator@localhost"), anyString(), anyString());

        assertThat(call(HttpMethod.POST, "/api/v1/reports", Map.of("targetType", "post", "targetId", "999999999", "reason", "spam"), reporter.token()))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("POST4001");
        assertThat(call(HttpMethod.POST, "/api/v1/reports", Map.of("targetType", "post", "targetId", postId, "reason", "boring"), reporter.token()))
                .hasStatus(400);
        assertThat(call(HttpMethod.POST, "/api/v1/reports", body, "dev:mr-guest")).hasStatus(401);
    }

    private MvcTestResult call(HttpMethod method, String uri, Object body, String token) {
        MockMvcRequestBuilder request = mvcTester.method(method).uri(uri);
        if (token != null) {
            request.header(token.startsWith("dev:") ? "X-Device-Id" : "Authorization", token.startsWith("dev:") ? token.substring(4) : "Bearer " + token);
        }
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body));
        }
        return request.exchange();
    }

    private List<String> ids(MvcTestResult res) {
        return read(res, "$.result.items[*].id");
    }

    private Member member(String email) {
        MemberFixture.plantSignupCode(jdbc, email);
        Map<String, Object> auth = result(call(HttpMethod.POST, "/api/v1/auth/signup",
                Map.of("email", email, "password", "pw123456", "nick", "회원", "code", MemberFixture.SIGNUP_CODE), null));
        String token = (String) auth.get("accessToken");
        assertThat(call(HttpMethod.PUT, "/api/v1/watch-groups/base/etfs", Map.of("codes", List.of("069500")), token)).hasStatus2xxSuccessful();
        return new Member(token, (String) ((Map<?, ?>) auth.get("me")).get("handle"));
    }

    private String post(Member m, String body) {
        return read(call(HttpMethod.POST, "/api/v1/posts", Map.of("body", body, "tags", List.of("069500")), m.token()), "$.result.id");
    }

    private String comment(Member m, String postId, String body) {
        return read(call(HttpMethod.POST, "/api/v1/posts/" + postId + "/comments", Map.of("body", body), m.token()), "$.result.id");
    }

    private int unread(Member m) {
        Number count = read(call(HttpMethod.GET, "/api/v1/notifications/unread-count", null, m.token()), "$.result.count");
        return count.intValue();
    }
}
