package com.edge.app.application.auth.required;

import com.edge.app.domain.member.Provider;

import java.util.Optional;

/**
 * 소셜 ID 토큰 검증
 * - 실패 시 빈 결과
 */
public interface SocialIdentityVerifier {
    /** email 은 제공자가 검증한 값만, 아니면 null */
    record Identity(String subject, String email) {
    }

    Optional<Identity> verify(Provider provider, String idToken, String nonce);
}
