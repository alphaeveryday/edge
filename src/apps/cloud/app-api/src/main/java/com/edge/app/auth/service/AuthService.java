package com.edge.app.auth.service;

import com.edge.app.auth.dto.AuthResponse;
import com.edge.app.auth.dto.LoginRequest;
import com.edge.app.auth.dto.PasswordResetRequest;
import com.edge.app.auth.dto.RefreshRequest;
import com.edge.app.auth.dto.SignupRequest;
import com.edge.app.auth.dto.SocialLoginRequest;
import com.edge.app.common.auth.AccessTokens;
import com.edge.app.member.service.MemberService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

/** 스텁. 액세스 토큰만 실제 발급(회원 1)해 앱이 회원 엔드포인트까지 이어 붙을 수 있게 한다. */
@Service
@RequiredArgsConstructor
public class AuthService {
    private static final long STUB_MEMBER_ID = 1L;

    private final AccessTokens tokens;
    private final MemberService memberService;

    public AuthResponse login(LoginRequest request) {
        return issue();
    }

    public AuthResponse social(SocialLoginRequest request) {
        return issue();
    }

    public AuthResponse signup(SignupRequest request) {
        return issue();
    }

    public void requestPasswordReset(PasswordResetRequest request) {
    }

    public void logout() {
    }

    public AuthResponse refresh(RefreshRequest request) {
        return issue();
    }

    private AuthResponse issue() {
        return new AuthResponse(tokens.issue(STUB_MEMBER_ID), "refreshToken", memberService.me(STUB_MEMBER_ID), false);
    }
}
