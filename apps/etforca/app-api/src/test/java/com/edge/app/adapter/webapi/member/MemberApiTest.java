package com.edge.app.adapter.webapi.member;

import com.edge.app.application.auth.provided.SignupRequest;
import com.edge.app.domain.member.MemberFixture;
import com.edge.app.support.stereotype.WebApiAdapterTest;
import com.jayway.jsonpath.JsonPath;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import tools.jackson.databind.ObjectMapper;

import java.io.UnsupportedEncodingException;
import java.util.Map;

import static com.edge.app.AssertThatUtils.notNull;
import static org.assertj.core.api.Assertions.assertThat;

@WebApiAdapterTest
record MemberApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper, JdbcTemplate jdbc) {
    @Test
    void updateNormalizesHandleAndRejectsTakenOne() throws UnsupportedEncodingException {
        String a = signup();
        String b = signup();

        assertThat(patchMe(a, Map.of("nick", "새닉", "handle", "orca_one")))
                .hasStatusOk()
                .bodyJson()
                .hasPathSatisfying("$.result.nick", value -> assertThat(value).isEqualTo("새닉"))
                // 앱 표기 형식 @ 를 서버가 보장
                .hasPathSatisfying("$.result.handle", value -> assertThat(value).isEqualTo("@orca_one"));
        assertThat(patchMe(b, Map.of("handle", "@orca_one"))).hasStatus(400);
        // 자기 handle 의 재사용 허용
        assertThat(patchMe(a, Map.of("handle", "@orca_one"))).hasStatusOk();
        assertThat(patchMe(a, Map.of("nick", ""))).hasStatus(400);
    }

    @Test
    void disclaimerAcceptanceIsRecordedOnce() throws UnsupportedEncodingException {
        String a = signup();

        assertThat(mvcTester.post().uri("/api/v1/me/disclaimer").header("Authorization", bearer(a)).exchange())
                .hasStatusOk()
                .bodyJson()
                .hasPathSatisfying("$.result.disclaimerAcceptedAt", notNull());
        // 저장 확인은 재조회로 하되 시계 해상도에 따라 갈리는 시각 정확 비교 제외
        assertThat(mvcTester.get().uri("/api/v1/me").header("Authorization", bearer(a)).exchange())
                .hasStatusOk()
                .bodyJson()
                .hasPathSatisfying("$.result.disclaimerAcceptedAt", notNull());
    }

    private String signup() throws UnsupportedEncodingException {
        SignupRequest request = MemberFixture.createSignupRequest();
        MemberFixture.plantSignupCode(jdbc, request.email());

        MvcTestResult result = mvcTester.post().uri("/api/v1/auth/signup").contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(request)).exchange();

        assertThat(result).hasStatusOk();
        return JsonPath.read(result.getResponse().getContentAsString(), "$.result.accessToken");
    }

    private MvcTestResult patchMe(String token, Map<String, String> body) {
        return mvcTester.patch().uri("/api/v1/me").header("Authorization", bearer(token)).contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(body)).exchange();
    }

    private static String bearer(String token) {
        return "Bearer " + token;
    }
}
