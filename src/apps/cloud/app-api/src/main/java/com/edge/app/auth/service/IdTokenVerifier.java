package com.edge.app.auth.service;

import com.edge.app.member.entity.Provider;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.jwk.source.JWKSourceBuilder;
import com.nimbusds.jose.proc.BadJOSEException;
import com.nimbusds.jose.proc.JWSVerificationKeySelector;
import com.nimbusds.jose.proc.SecurityContext;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.proc.DefaultJWTClaimsVerifier;
import com.nimbusds.jwt.proc.DefaultJWTProcessor;
import com.nimbusds.jwt.proc.JWTProcessor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Component;

import java.net.MalformedURLException;
import java.net.URI;
import java.text.ParseException;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;

/**
 * 실패 시 빈 결과를 내는 소셜 idToken 의 JWKS 검증
 * 검증된 이메일만 Identity 에 싣기
 */
@Component
@EnableConfigurationProperties(SocialProperties.class)
public class IdTokenVerifier {
    /** email 은 제공자가 검증한 값만, 아니면 null */
    public record Identity(String subject, String email) {
    }

    /** email_verified 클레임 판정과, 검증된 이메일만 싣는 제공자(카카오)의 email 존재 판정 */
    enum EmailTrust { CLAIM, PRESENT }

    /** 앱이 보낸 nonce 와 토큰 nonce 클레임의 그대로 대조, RAW 는 앱 값 필수 */
    enum NonceCheck { NONE, RAW }

    record Rule(JWTProcessor<SecurityContext> processor, EmailTrust emailTrust, NonceCheck nonceCheck) {
    }

    private static final String APPLE_ISSUER = "https://appleid.apple.com";
    private static final String APPLE_JWKS = "https://appleid.apple.com/auth/keys";
    private static final String GOOGLE_ISSUER = "https://accounts.google.com";
    private static final String GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs";
    private static final String KAKAO_ISSUER = "https://kauth.kakao.com";
    private static final String KAKAO_JWKS = "https://kauth.kakao.com/.well-known/jwks.json";

    private final Map<Provider, Rule> rules;

    @Autowired
    public IdTokenVerifier(SocialProperties properties) throws MalformedURLException {
        this(Map.of(
                Provider.APPLE, new Rule(processor(remote(APPLE_JWKS), APPLE_ISSUER, properties.appleClientId()),
                        EmailTrust.CLAIM, NonceCheck.NONE),
                Provider.GOOGLE, new Rule(processor(remote(GOOGLE_JWKS), GOOGLE_ISSUER, properties.googleClientId()),
                        EmailTrust.CLAIM, NonceCheck.NONE),
                Provider.KAKAO, new Rule(processor(remote(KAKAO_JWKS), KAKAO_ISSUER, properties.kakaoClientId()),
                        EmailTrust.PRESENT, NonceCheck.RAW)));
    }

    IdTokenVerifier(Map<Provider, Rule> rules) {
        this.rules = rules;
    }

    public Optional<Identity> verify(Provider provider, String idToken, String nonce) {
        Rule rule = rules.get(provider);
        if (rule == null) {
            return Optional.empty();
        }
        try {
            JWTClaimsSet claims = rule.processor().process(idToken, null);
            if (rule.nonceCheck() == NonceCheck.RAW && (nonce == null || !nonce.equals(claims.getStringClaim("nonce")))) {
                return Optional.empty();
            }
            return Optional.of(new Identity(claims.getSubject(), verifiedEmail(claims, rule.emailTrust())));
        } catch (ParseException | BadJOSEException | com.nimbusds.jose.JOSEException e) {
            return Optional.empty();
        }
    }

    private static String verifiedEmail(JWTClaimsSet claims, EmailTrust trust) throws ParseException {
        String email = claims.getStringClaim("email");
        if (email == null || trust == EmailTrust.PRESENT) {
            return email;
        }
        Object verified = claims.getClaim("email_verified");
        return Boolean.TRUE.equals(verified) || Objects.equals("true", verified) ? email : null;
    }

    private static JWKSource<SecurityContext> remote(String url) throws MalformedURLException {
        return JWKSourceBuilder.create(URI.create(url).toURL()).build();
    }

    static JWTProcessor<SecurityContext> processor(JWKSource<SecurityContext> keys, String issuer, String audience) {
        DefaultJWTProcessor<SecurityContext> processor = new DefaultJWTProcessor<>();
        processor.setJWSKeySelector(new JWSVerificationKeySelector<>(JWSAlgorithm.RS256, keys));
        // audience 미설정 시 빈 문자열 요구로 전부 거절
        String required = audience == null || audience.isBlank() ? "" : audience;
        processor.setJWTClaimsSetVerifier(new DefaultJWTClaimsVerifier<>(required,
                new JWTClaimsSet.Builder().issuer(issuer).build(), Set.of("sub", "exp")));
        return processor;
    }
}
