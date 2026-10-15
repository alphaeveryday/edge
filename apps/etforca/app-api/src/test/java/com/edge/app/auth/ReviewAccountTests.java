package com.edge.app.auth;

import com.edge.app.ContainerTests;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;

import java.util.Map;

import static com.edge.app.ApiCalls.call;
import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * 메일을 받을 수 없는 심사자를 위한 지정 이메일 하나의 고정 코드 가입과 재설정
 * 다른 이메일에서의 고정 코드 거부
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = "app.review.email=review-test@example.com")
class ReviewAccountTests extends ContainerTests {
    @LocalServerPort
    int port;

    @Test
    void reviewEmailSignsUpAndResetsWithFixedCode() {
        String email = "review-test@example.com";
        assertEquals(200, call(port, "POST", "/api/v1/auth/signup/code", Map.of("email", email)).getStatusCode().value());
        assertEquals("COMMON200", call(port, "POST", "/api/v1/auth/signup",
                Map.of("email", email, "password", "review1234", "nick", "심사", "code", "123456"), "X-Device-Id", "review-1").getBody().get("code"));

        assertEquals(200, call(port, "POST", "/api/v1/auth/password-reset", Map.of("email", email)).getStatusCode().value());
        assertEquals(200, call(port, "POST", "/api/v1/auth/password-reset/confirm",
                Map.of("email", email, "code", "123456", "newPassword", "review5678")).getStatusCode().value());
        assertEquals(200, call(port, "POST", "/api/v1/auth/login", Map.of("email", email, "password", "review5678"),
                "X-Device-Id", "review-1").getStatusCode().value());
    }

    @Test
    void otherEmailDoesNotAcceptFixedCode() {
        String email = "not-review@example.com";
        call(port, "POST", "/api/v1/auth/signup/code", Map.of("email", email));
        assertEquals("AUTH4002", call(port, "POST", "/api/v1/auth/signup",
                Map.of("email", email, "password", "review1234", "nick", "남", "code", "123456"), "X-Device-Id", "review-2").getBody().get("code"));
    }
}
