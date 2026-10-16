package com.edge.app.application.auth.provided;

/**
 * 비밀번호 재설정 코드 발송과 확인
 */
public interface PasswordResetter {
    void requestPasswordReset(PasswordResetRequest request);

    void confirmPasswordReset(PasswordResetConfirmRequest request);
}
