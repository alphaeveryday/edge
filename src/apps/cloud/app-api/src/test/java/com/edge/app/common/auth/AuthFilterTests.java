package com.edge.app.common.auth;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.exception.ExceptionAdvice;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 계약의 security 두 종류(bearer 전용 / bearer 또는 deviceId)를 인자 타입이 그대로 표현하는지. */
class AuthFilterTests {
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
    MockMvc mvc;

    @BeforeEach
    void setUp() throws Exception {
        tokens = new AccessTokens(new JwtProperties("0123456789abcdef0123456789abcdef", Duration.ofHours(1)));
        mvc = MockMvcBuilders.standaloneSetup(new Probe())
                .addFilters(new AuthFilter(tokens, new ObjectMapper()))
                .setCustomArgumentResolvers(new PrincipalArgumentResolver())
                .setControllerAdvice(new ExceptionAdvice())
                .build();
    }

    @Test
    void bearerResolvesMember() throws Exception {
        mvc.perform(get("/member").header("Authorization", "Bearer " + tokens.issue(7L)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.result").value(7));
    }

    @Test
    void invalidBearerIsUnauthorizedEvenWithDeviceId() throws Exception {
        // 토큰이 있으면 토큰이 우선이고 검증 실패를 게스트로 강등하지 않는다.
        mvc.perform(get("/any").header("Authorization", "Bearer bad").header("X-Device-Id", "d1"))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("COMMON401"));
        mvc.perform(get("/public").header("Authorization", "Basic abc"))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("COMMON401"));
    }

    @Test
    void bearerWinsOverDeviceId() throws Exception {
        mvc.perform(get("/any").header("Authorization", "Bearer " + tokens.issue(7L)).header("X-Device-Id", "d1"))
                .andExpect(jsonPath("$.result.kind").value("MEMBER"))
                .andExpect(jsonPath("$.result.memberId").value(7));
    }

    @Test
    void deviceIdResolvesGuestOnlyWhereGuestIsAllowed() throws Exception {
        mvc.perform(get("/any").header("X-Device-Id", " d1 "))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.result.kind").value("DEVICE"))
                .andExpect(jsonPath("$.result.deviceKey").value("d1"));
        mvc.perform(get("/member").header("X-Device-Id", "d1"))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("COMMON401"));
    }

    @Test
    void anonymousPassesOnlyPublicEndpoints() throws Exception {
        mvc.perform(get("/public")).andExpect(status().isOk());
        mvc.perform(get("/any")).andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value("COMMON401"));
        mvc.perform(get("/member")).andExpect(status().isUnauthorized());
    }

    @Test
    void malformedDeviceIdIsBadRequest() throws Exception {
        mvc.perform(get("/any").header("X-Device-Id", "  ")).andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("COMMON400"));
        mvc.perform(get("/any").header("X-Device-Id", "x".repeat(65))).andExpect(status().isBadRequest());
    }
}
