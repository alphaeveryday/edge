package com.edge.app.common.auth;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class AccessTokensTests {
    static final String SECRET = "test-jwt-secret-for-tests-only-32bytes";
    static final Instant NOW = Instant.parse("2026-09-28T00:00:00Z");

    static AccessTokens at(Instant now, String secret) throws Exception {
        return new AccessTokens(new JwtProperties(secret, Duration.ofHours(1)), Clock.fixed(now, ZoneOffset.UTC));
    }

    @Test
    void issuedTokenCarriesMemberId() throws Exception {
        AccessTokens tokens = at(NOW, SECRET);
        assertEquals(Optional.of(7L), tokens.verify(tokens.issue(7L)));
    }

    @Test
    void expiredTokenIsRejected() throws Exception {
        String token = at(NOW, SECRET).issue(7L);
        assertEquals(Optional.empty(), at(NOW.plus(Duration.ofHours(1)), SECRET).verify(token));
    }

    @Test
    void tokenSignedWithOtherSecretIsRejected() throws Exception {
        String token = at(NOW, SECRET).issue(7L);
        assertEquals(Optional.empty(), at(NOW, "fedcba9876543210fedcba9876543210").verify(token));
        assertEquals(Optional.empty(), at(NOW, SECRET).verify("not-a-jwt"));
    }

    @Test
    void shortSecretFailsAtStartup() {
        assertThrows(Exception.class, () -> at(NOW, "short"));
    }
}
