package com.edge.app.application.auth.provided;

import com.edge.app.application.member.provided.AppPrincipal;

/**
 * 가입·로그인과 토큰 재발급·로그아웃
 * - deviceKey 가 있을 때의 게스트 데이터 계정 매핑
 */
public interface Authenticator {
    AuthResult login(LoginRequest request, String deviceKey);

    AuthResult social(SocialLoginRequest request, String deviceKey);

    AuthResult signup(SignupRequest request, String deviceKey);

    AuthResult refresh(RefreshRequest request);

    void logout(AppPrincipal principal);
}
