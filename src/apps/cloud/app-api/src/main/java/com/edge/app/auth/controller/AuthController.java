package com.edge.app.auth.controller;

import com.edge.app.auth.dto.AuthResponse;
import com.edge.app.auth.dto.LoginRequest;
import com.edge.app.auth.dto.PasswordResetRequest;
import com.edge.app.auth.dto.RefreshRequest;
import com.edge.app.auth.dto.SignupRequest;
import com.edge.app.auth.dto.SocialLoginRequest;
import com.edge.app.auth.service.AuthService;
import com.edge.app.common.auth.AppPrincipal;
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

    private final AuthService authService;

    // X-Device-Id 선택. 있으면 게스트 데이터의 계정 매핑
    @PostMapping("/login")
    public ApiResponse<AuthResponse> authLogin(@RequestBody @Valid LoginRequest request,
            @RequestHeader(value = DEVICE_HEADER, required = false) String deviceKey) {
        return ApiResponse.onSuccess(authService.login(request, deviceKey));
    }

    @PostMapping("/social")
    public ApiResponse<AuthResponse> authSocial(@RequestBody @Valid SocialLoginRequest request,
            @RequestHeader(value = DEVICE_HEADER, required = false) String deviceKey) {
        return ApiResponse.onSuccess(authService.social(request, deviceKey));
    }

    @PostMapping("/signup")
    public ApiResponse<AuthResponse> authSignup(@RequestBody @Valid SignupRequest request,
            @RequestHeader(value = DEVICE_HEADER, required = false) String deviceKey) {
        return ApiResponse.onSuccess(authService.signup(request, deviceKey));
    }

    @PostMapping("/password-reset")
    public ApiResponse<Void> authRequestPasswordReset(@RequestBody @Valid PasswordResetRequest request) {
        authService.requestPasswordReset(request);
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/logout")
    public ApiResponse<Void> authLogout(AppPrincipal principal) {
        authService.logout(principal);
        return ApiResponse.onSuccess(null);
    }

    @PostMapping("/refresh")
    public ApiResponse<AuthResponse> authRefresh(@RequestBody @Valid RefreshRequest request) {
        return ApiResponse.onSuccess(authService.refresh(request));
    }
}
