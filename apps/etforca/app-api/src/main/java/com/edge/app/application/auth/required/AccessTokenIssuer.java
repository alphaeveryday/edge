package com.edge.app.application.auth.required;

/**
 * 회원 액세스 토큰 발급
 */
public interface AccessTokenIssuer {
    String issue(long memberId);
}
