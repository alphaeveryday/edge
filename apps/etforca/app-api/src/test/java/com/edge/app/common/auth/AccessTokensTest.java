package com.edge.app.common.auth;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AccessTokensTest {
    static final String SECRET = "test-jwt-secret-for-tests-only-32bytes";
    static final Instant NOW = Instant.parse("2026-09-28T00:00:00Z");

    static AccessTokens at(Instant now, String secret) throws Exception {
        return new AccessTokens(new JwtProperties(secret, Duration.ofHours(1)), Clock.fixed(now, ZoneOffset.UTC));
    }

    @Test
    void issuedTokenCarriesMemberId() throws Exception {
        AccessTokens tokens = at(NOW, SECRET);
        assertThat(tokens.verify(tokens.issue(7L))).hasValue(7L);
    }

    @Test
    void expiredTokenIsRejected() throws Exception {
        String token = at(NOW, SECRET).issue(7L);
        assertThat(at(NOW.plus(Duration.ofHours(1)), SECRET).verify(token)).isEmpty();
    }

    @Test
    void tokenSignedWithOtherSecretIsRejected() throws Exception {
        String token = at(NOW, SECRET).issue(7L);
        assertThat(at(NOW, "fedcba9876543210fedcba9876543210").verify(token)).isEmpty();
        assertThat(at(NOW, SECRET).verify("not-a-jwt")).isEmpty();
    }

    @Test
    void shortSecretFailsAtStartup() {
        assertThatThrownBy(() -> at(NOW, "short")).isInstanceOf(Exception.class);
    }
}
