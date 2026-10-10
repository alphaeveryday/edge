package com.edge.app.auth.service;

import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.ECDSASigner;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;

import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.interfaces.ECPrivateKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.Date;
import java.util.Map;

/**
 * 애플 authorization code 의 refresh token 교환과 탈퇴 시 철회
 * 키 미설정·애플 오류는 로그만 남기고 로그인·탈퇴 진행
 */
@Slf4j
@Component
public class AppleTokenService {
    private static final String APPLE = "https://appleid.apple.com";
    private static final Duration SECRET_TTL = Duration.ofMinutes(5);

    private final SocialProperties properties;
    private final RestClient http;

    @Autowired
    public AppleTokenService(SocialProperties properties) {
        this(properties, RestClient.create(APPLE));
    }

    AppleTokenService(SocialProperties properties, RestClient http) {
        this.properties = properties;
        this.http = http;
    }

    // 실패 시 null
    public String exchange(String authorizationCode) {
        if (authorizationCode == null || !configured()) {
            return null;
        }
        try {
            Map<?, ?> body = http.post().uri("/auth/token").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form("code", authorizationCode, "grant_type", "authorization_code"))
                    .retrieve().body(Map.class);
            return body == null ? null : (String) body.get("refresh_token");
        } catch (RuntimeException | GeneralSecurityException | JOSEException e) {
            log.warn("apple token exchange failed", e);
            return null;
        }
    }

    public void revoke(String refreshToken) {
        if (!configured()) {
            log.warn("apple token revoke skipped reason=unconfigured");
            return;
        }
        try {
            http.post().uri("/auth/revoke").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form("token", refreshToken, "token_type_hint", "refresh_token"))
                    .retrieve().toBodilessEntity();
        } catch (RuntimeException | GeneralSecurityException | JOSEException e) {
            log.warn("apple token revoke failed", e);
        }
    }

    private boolean configured() {
        return notBlank(properties.appleClientId()) && notBlank(properties.appleTeamId())
                && notBlank(properties.appleKeyId()) && notBlank(properties.applePrivateKey());
    }

    private MultiValueMap<String, String> form(String... pairs) throws GeneralSecurityException, JOSEException {
        MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
        form.add("client_id", properties.appleClientId());
        form.add("client_secret", clientSecret(Instant.now()));
        for (int i = 0; i < pairs.length; i += 2) {
            form.add(pairs[i], pairs[i + 1]);
        }
        return form;
    }

    // 애플 개발자 키로 서명한 ES256 client secret
    String clientSecret(Instant now) throws GeneralSecurityException, JOSEException {
        JWTClaimsSet claims = new JWTClaimsSet.Builder().issuer(properties.appleTeamId()).subject(properties.appleClientId())
                .audience(APPLE).issueTime(Date.from(now)).expirationTime(Date.from(now.plus(SECRET_TTL))).build();
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.ES256).keyID(properties.appleKeyId()).build(), claims);
        jwt.sign(new ECDSASigner(privateKey()));
        return jwt.serialize();
    }

    // 환경 변수의 줄바꿈이 \n 문자열로 들어온 PEM 도 허용
    private ECPrivateKey privateKey() throws GeneralSecurityException {
        String pem = properties.applePrivateKey().replace("\\n", "\n")
                .replaceAll("-----(BEGIN|END) PRIVATE KEY-----", "").replaceAll("\\s", "");
        return (ECPrivateKey) KeyFactory.getInstance("EC").generatePrivate(new PKCS8EncodedKeySpec(Base64.getDecoder().decode(pem)));
    }

    private static boolean notBlank(String s) {
        return s != null && !s.isBlank();
    }
}
