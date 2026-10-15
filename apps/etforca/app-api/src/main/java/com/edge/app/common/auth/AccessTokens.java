package com.edge.app.common.auth;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.MACSigner;
import com.nimbusds.jose.crypto.MACVerifier;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.text.ParseException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.Optional;

/** sub·iat·exp 클레임의 액세스 JWT 발급·검증 */
@Component
public class AccessTokens {
    private final MACSigner signer;
    private final MACVerifier verifier;
    private final Duration ttl;
    private final Clock clock;

    @Autowired
    public AccessTokens(JwtProperties properties) throws JOSEException {
        this(properties, Clock.systemUTC());
    }

    AccessTokens(JwtProperties properties, Clock clock) throws JOSEException {
        byte[] secret = properties.secret().getBytes(StandardCharsets.UTF_8);
        this.signer = new MACSigner(secret);
        this.verifier = new MACVerifier(secret);
        this.ttl = properties.accessTtl();
        this.clock = clock;
    }

    public String issue(long memberId) {
        Instant now = clock.instant();
        JWTClaimsSet claims = new JWTClaimsSet.Builder()
                .subject(Long.toString(memberId))
                .issueTime(Date.from(now))
                .expirationTime(Date.from(now.plus(ttl)))
                .build();
        SignedJWT jwt = new SignedJWT(new JWSHeader(JWSAlgorithm.HS256), claims);
        try {
            jwt.sign(signer);
        } catch (JOSEException e) {
            throw new IllegalStateException("JWT 서명 실패", e);
        }
        return jwt.serialize();
    }

    /** 실패 시 빈 결과를 내는 서명·만료·sub 검증 */
    public Optional<Long> verify(String token) {
        try {
            SignedJWT jwt = SignedJWT.parse(token);
            if (!jwt.verify(verifier)) {
                return Optional.empty();
            }
            JWTClaimsSet claims = jwt.getJWTClaimsSet();
            Date exp = claims.getExpirationTime();
            if (exp == null || !exp.toInstant().isAfter(clock.instant())) {
                return Optional.empty();
            }
            return Optional.of(Long.parseLong(claims.getSubject()));
        } catch (ParseException | JOSEException | NumberFormatException | NullPointerException e) {
            return Optional.empty();
        }
    }
}
