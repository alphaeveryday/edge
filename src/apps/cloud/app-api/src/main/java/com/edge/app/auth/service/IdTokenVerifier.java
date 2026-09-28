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
import java.util.Optional;
import java.util.Set;

/** Apple·Google idToken 의 JWKS 검증. 결과는 sub·email, 실패는 empty */
@Component
@EnableConfigurationProperties(SocialProperties.class)
public class IdTokenVerifier {
    public record Identity(String subject, String email) {
    }

    private static final String APPLE_ISSUER = "https://appleid.apple.com";
    private static final String APPLE_JWKS = "https://appleid.apple.com/auth/keys";
    private static final String GOOGLE_ISSUER = "https://accounts.google.com";
    private static final String GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs";

    private final Map<Provider, JWTProcessor<SecurityContext>> processors;

    @Autowired
    public IdTokenVerifier(SocialProperties properties) throws MalformedURLException {
        this(Map.of(
                Provider.APPLE, processor(remote(APPLE_JWKS), APPLE_ISSUER, properties.appleClientId()),
                Provider.GOOGLE, processor(remote(GOOGLE_JWKS), GOOGLE_ISSUER, properties.googleClientId())));
    }

    IdTokenVerifier(Map<Provider, JWTProcessor<SecurityContext>> processors) {
        this.processors = processors;
    }

    public Optional<Identity> verify(Provider provider, String idToken) {
        JWTProcessor<SecurityContext> processor = processors.get(provider);
        if (processor == null) {
            return Optional.empty();
        }
        try {
            JWTClaimsSet claims = processor.process(idToken, null);
            return Optional.of(new Identity(claims.getSubject(), claims.getStringClaim("email")));
        } catch (ParseException | BadJOSEException | com.nimbusds.jose.JOSEException e) {
            return Optional.empty();
        }
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
