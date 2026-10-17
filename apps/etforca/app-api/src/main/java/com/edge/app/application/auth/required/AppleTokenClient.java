package com.edge.app.application.auth.required;

/**
 * 애플 refresh token 교환과 철회
 * - 교환 실패 시 null
 */
public interface AppleTokenClient {
    String exchange(String authorizationCode);

    void revoke(String refreshToken);
}
