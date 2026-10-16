package com.edge.app.application.auth.provided;

/**
 * 가입 이메일 인증 코드 발송과 확인
 */
public interface SignupCodeVerifier {
    void send(String email);

    void verify(String email, String code);
}
