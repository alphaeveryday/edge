package com.edge.app.adapter.webapi.auth;

import com.edge.app.adapter.webapi.auth.dto.AuthResponse;
import com.edge.app.adapter.webapi.auth.dto.SignupCodeRequest;
import com.edge.app.application.auth.provided.Authenticator;
import com.edge.app.application.auth.provided.LoginRequest;
import com.edge.app.application.auth.provided.PasswordResetConfirmRequest;
import com.edge.app.application.auth.provided.PasswordResetRequest;
import com.edge.app.application.auth.provided.PasswordResetter;
import com.edge.app.application.auth.provided.RefreshRequest;
import com.edge.app.application.auth.provided.SignupCodeVerifier;
import com.edge.app.application.auth.provided.SignupRequest;
import com.edge.app.application.auth.provided.SocialLoginRequest;
import com.edge.app.application.member.provided.AppPrincipal;
import com.edge.common.apipayload.ApiResponse;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/auth")
@RequiredArgsConstructor
public class AuthController {
    private static final String DEVICE_HEADER = "X-Device-Id";

    private final Authenticator authenticator;
    private final PasswordResetter passwordResetter;
    private final SignupCodeVerifier signupCodeVerifier;

    // X-Device-Id 가 있을 때의 게스트 데이터 계정 매핑
    @PostMapping("/login")
    public ApiResponse<AuthResponse> authLogin(@RequestBody @Valid LoginRequest request,
            @RequestHeader(value = DEVICE_HEADER, required = false) String deviceKey) {
        return ApiResponse.onSuccess(AuthResponse.from(authenticator.login(request, deviceKey)));
    }

    @PostMapping("/social")
    public ApiResponse<AuthResponse> authSocial(@RequestBody @Valid SocialLoginRequest request,
            @RequestHeader(value = DEVICE_HEADER, required = false) String deviceKey) {
        return ApiResponse.onSuccess(AuthResponse.from(authenticator.social(request, deviceKey)));
    }

    @PostMapping("/signup/code")
    public ApiResponse<Void> authSendSignupCode(@RequestBody @Valid SignupCodeRequest request) {
        signupCodeVerifier.send(request.email());
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/signup")
    public ApiResponse<AuthResponse> authSignup(@RequestBody @Valid SignupRequest request,
            @RequestHeader(value = DEVICE_HEADER, required = false) String deviceKey) {
        // 요청당 DB 연결 두 개 점유를 막는 가입 트랜잭션 밖 코드 확인 선행
        signupCodeVerifier.verify(request.email(), request.code());
        return ApiResponse.onSuccess(AuthResponse.from(authenticator.signup(request, deviceKey)));
    }

    @PostMapping("/password-reset")
    public ApiResponse<Void> authRequestPasswordReset(@RequestBody @Valid PasswordResetRequest request) {
        passwordResetter.requestPasswordReset(request);
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/password-reset/confirm")
    public ApiResponse<Void> authConfirmPasswordReset(@RequestBody @Valid PasswordResetConfirmRequest request) {
        passwordResetter.confirmPasswordReset(request);
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/logout")
    public ApiResponse<Void> authLogout(AppPrincipal principal) {
        authenticator.logout(principal);
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/refresh")
    public ApiResponse<AuthResponse> authRefresh(@RequestBody @Valid RefreshRequest request) {
        return ApiResponse.onSuccess(AuthResponse.from(authenticator.refresh(request)));
    }
}
