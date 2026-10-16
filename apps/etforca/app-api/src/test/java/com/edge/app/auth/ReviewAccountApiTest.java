package com.edge.app.auth;

import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import tools.jackson.databind.ObjectMapper;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 메일을 받을 수 없는 심사자를 위한 지정 이메일 하나의 고정 코드 가입과 재설정
 * 다른 이메일에서의 고정 코드 거부
 */
@WebApiAdapterTest
@TestPropertySource(properties = "app.review.email=review-test@example.com")
record ReviewAccountApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper) {
    @Test
    void reviewEmailSignsUpAndResetsWithFixedCode() {
        String email = "review-test@example.com";
        assertThat(post("/api/v1/auth/signup/code", Map.of("email", email))).hasStatusOk();
        assertThat(post("/api/v1/auth/signup", Map.of("email", email, "password", "review1234", "nick", "심사", "code", "123456"), "review-1"))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("COMMON200");

        assertThat(post("/api/v1/auth/password-reset", Map.of("email", email))).hasStatusOk();
        assertThat(post("/api/v1/auth/password-reset/confirm", Map.of("email", email, "code", "123456", "newPassword", "review5678"))).hasStatusOk();
        assertThat(post("/api/v1/auth/login", Map.of("email", email, "password", "review5678"), "review-1")).hasStatusOk();
    }

    @Test
    void otherEmailDoesNotAcceptFixedCode() {
        String email = "not-review@example.com";
        post("/api/v1/auth/signup/code", Map.of("email", email));
        assertThat(post("/api/v1/auth/signup", Map.of("email", email, "password", "review1234", "nick", "남", "code", "123456"), "review-2"))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("AUTH4002");
    }

    private MvcTestResult post(String uri, Object body) {
        return mvcTester.post().uri(uri).contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body)).exchange();
    }

    private MvcTestResult post(String uri, Object body, String device) {
        return mvcTester.post().uri(uri).header("X-Device-Id", device)
                .contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body)).exchange();
    }
}
