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

/** 액세스 JWT 발급·검증. 클레임은 sub(memberId)·iat·exp 만. 리프레시는 DB 저장이라 여기 없다. */
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

    /** 서명·만료·sub 중 하나라도 어긋나면 empty. */
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
