package com.edge.app.adapter.security;

import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.common.apipayload.ApiResponse;
import com.edge.common.exception.ExceptionAdvice;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;

/** 계약의 security 두 종류를 인자 타입으로 표현하는 인증 필터 */
class AuthFilterTest {
    @RestController
    static class Probe {
        @GetMapping("/member")
        ApiResponse<Long> member(MemberPrincipal p) {
            return ApiResponse.onSuccess(p.memberId());
        }

        @GetMapping("/any")
        ApiResponse<AppPrincipal> any(AppPrincipal p) {
            return ApiResponse.onSuccess(p);
        }

        @GetMapping("/public")
        ApiResponse<Void> open() {
            return ApiResponse.onSuccess(null);
        }
    }

    AccessTokens tokens;
    MockMvcTester mvc;

    @BeforeEach
    void setUp() throws Exception {
        tokens = new AccessTokens(new JwtProperties("test-jwt-secret-for-tests-only-32bytes", Duration.ofHours(1)));
        mvc = MockMvcTester.create(MockMvcBuilders.standaloneSetup(new Probe())
                .addFilters(new AuthFilter(tokens, new ObjectMapper()))
                .setCustomArgumentResolvers(new PrincipalArgumentResolver())
                .setControllerAdvice(new ExceptionAdvice())
                .build());
    }

    @Test
    void bearerResolvesMember() throws Exception {
        assertThat(mvc.get().uri("/member").header("Authorization", "Bearer " + tokens.issue(7L)))
                .hasStatusOk().bodyJson().extractingPath("$.result").isEqualTo(7);
    }

    @Test
    void invalidBearerIsUnauthorizedEvenWithDeviceId() {
        // 토큰 우선 처리와 검증 실패의 게스트 강등 금지
        assertThat(mvc.get().uri("/any").header("Authorization", "Bearer bad").header("X-Device-Id", "d1"))
                .hasStatus(401).bodyJson().extractingPath("$.code").isEqualTo("COMMON401");
        assertThat(mvc.get().uri("/public").header("Authorization", "Basic abc"))
                .hasStatus(401).bodyJson().extractingPath("$.code").isEqualTo("COMMON401");
    }

    @Test
    void bearerWinsOverDeviceId() throws Exception {
        assertThat(mvc.get().uri("/any").header("Authorization", "Bearer " + tokens.issue(7L)).header("X-Device-Id", "d1"))
                .bodyJson()
                .hasPathSatisfying("$.result.kind", value -> assertThat(value).isEqualTo("MEMBER"))
                .hasPathSatisfying("$.result.memberId", value -> assertThat(value).isEqualTo(7));
    }

    @Test
    void deviceIdResolvesGuestOnlyWhereGuestIsAllowed() {
        assertThat(mvc.get().uri("/any").header("X-Device-Id", " d1 "))
                .hasStatusOk()
                .bodyJson()
                .hasPathSatisfying("$.result.kind", value -> assertThat(value).isEqualTo("DEVICE"))
                .hasPathSatisfying("$.result.deviceKey", value -> assertThat(value).isEqualTo("d1"));
        assertThat(mvc.get().uri("/member").header("X-Device-Id", "d1"))
                .hasStatus(401).bodyJson().extractingPath("$.code").isEqualTo("COMMON401");
    }

    @Test
    void anonymousPassesOnlyPublicEndpoints() {
        assertThat(mvc.get().uri("/public")).hasStatusOk();
        assertThat(mvc.get().uri("/any")).hasStatus(401).bodyJson().extractingPath("$.code").isEqualTo("COMMON401");
        assertThat(mvc.get().uri("/member")).hasStatus(401);
    }

    @Test
    void malformedDeviceIdIsBadRequest() {
        assertThat(mvc.get().uri("/any").header("X-Device-Id", "  "))
                .hasStatus(400).bodyJson().extractingPath("$.code").isEqualTo("COMMON400");
        assertThat(mvc.get().uri("/any").header("X-Device-Id", "x".repeat(65))).hasStatus(400);
    }
}
