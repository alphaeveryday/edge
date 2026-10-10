package com.edge.app.auth.service;

import com.edge.app.member.entity.Provider;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import org.junit.jupiter.api.Test;

import java.util.Date;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 서명, 발급자, audience, 만료, nonce 중 하나라도 어긋난 토큰의 로그인 거부 */
class IdTokenVerifierTests {
    @Test
    void acceptsOnlyTokensSignedByIssuerForOurAudience() throws Exception {
        RSAKey key = new RSAKeyGenerator(2048).keyID("k1").generate();
        RSAKey other = new RSAKeyGenerator(2048).keyID("k2").generate();
        var verifier = new IdTokenVerifier(Map.of(Provider.APPLE, rule(key, "com.edge.orca",
                IdTokenVerifier.EmailTrust.CLAIM, IdTokenVerifier.NonceCheck.NONE)));
        Date future = new Date(System.currentTimeMillis() + 60_000);

        String good = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("com.edge.orca").expirationTime(future).claim("email", "e@x.com").claim("email_verified", "true").build());
        assertEquals(new IdTokenVerifier.Identity("s1", "e@x.com"), verifier.verify(Provider.APPLE, good, null).orElseThrow());

        String wrongKey = sign(other, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("com.edge.orca").expirationTime(future).build());
        String wrongAud = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("someone.else").expirationTime(future).build());
        String wrongIss = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://accounts.google.com")
                .audience("com.edge.orca").expirationTime(future).build());
        String expired = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("com.edge.orca").expirationTime(new Date(0)).build());
        for (String bad : new String[] {wrongKey, wrongAud, wrongIss, expired, "garbage"}) {
            assertTrue(verifier.verify(Provider.APPLE, bad, null).isEmpty());
        }
        assertTrue(verifier.verify(Provider.GOOGLE, good, null).isEmpty(), "설정 없는 provider 는 거절");
    }

    @Test
    void unconfiguredAudienceRejectsEverything() throws Exception {
        RSAKey key = new RSAKeyGenerator(2048).keyID("k1").generate();
        var verifier = new IdTokenVerifier(Map.of(Provider.APPLE, rule(key, "",
                IdTokenVerifier.EmailTrust.CLAIM, IdTokenVerifier.NonceCheck.NONE)));
        String token = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("com.edge.orca").expirationTime(new Date(System.currentTimeMillis() + 60_000)).build());
        assertTrue(verifier.verify(Provider.APPLE, token, null).isEmpty());
    }

    /** email_verified 가 참일 때만 이메일 보존 */
    @Test
    void keepsEmailOnlyWhenVerifiedClaimIsTrue() throws Exception {
        RSAKey key = new RSAKeyGenerator(2048).keyID("k1").generate();
        var verifier = new IdTokenVerifier(Map.of(Provider.GOOGLE, rule(key, "aud",
                IdTokenVerifier.EmailTrust.CLAIM, IdTokenVerifier.NonceCheck.NONE)));
        assertEquals("e@x.com", verifier.verify(Provider.GOOGLE, sign(key, claims("aud").claim("email_verified", true).build()), null).orElseThrow().email());
        assertEquals("e@x.com", verifier.verify(Provider.GOOGLE, sign(key, claims("aud").claim("email_verified", "true").build()), null).orElseThrow().email());
        assertNull(verifier.verify(Provider.GOOGLE, sign(key, claims("aud").claim("email_verified", false).build()), null).orElseThrow().email());
        assertNull(verifier.verify(Provider.GOOGLE, sign(key, claims("aud").build()), null).orElseThrow().email());
    }

    /** 카카오는 email 존재가 곧 검증, nonce 는 앱 값과 같아야 통과 */
    @Test
    void kakaoTrustsPresentEmailAndRequiresMatchingNonce() throws Exception {
        RSAKey key = new RSAKeyGenerator(2048).keyID("k1").generate();
        var verifier = new IdTokenVerifier(Map.of(Provider.KAKAO, rule(key, "app-key",
                IdTokenVerifier.EmailTrust.PRESENT, IdTokenVerifier.NonceCheck.RAW)));
        String token = sign(key, claims("app-key").claim("nonce", "n-1").build());
        assertEquals("e@x.com", verifier.verify(Provider.KAKAO, token, "n-1").orElseThrow().email());
        assertTrue(verifier.verify(Provider.KAKAO, token, "n-2").isEmpty());
        assertTrue(verifier.verify(Provider.KAKAO, token, null).isEmpty());
        assertTrue(verifier.verify(Provider.KAKAO, sign(key, claims("app-key").build()), "n-1").isEmpty());
    }

    /** 애플은 앱이 보낸 원문의 SHA-256 16진 값과 토큰 nonce 대조 */
    @Test
    void appleComparesSha256OfRawNonce() throws Exception {
        RSAKey key = new RSAKeyGenerator(2048).keyID("k1").generate();
        var verifier = new IdTokenVerifier(Map.of(Provider.APPLE, rule(key, "bundle",
                IdTokenVerifier.EmailTrust.CLAIM, IdTokenVerifier.NonceCheck.SHA256)));
        String token = sign(key, claims("bundle").claim("nonce", AuthService.hash("raw-1")).build());
        assertTrue(verifier.verify(Provider.APPLE, token, "raw-1").isPresent());
        assertTrue(verifier.verify(Provider.APPLE, token, AuthService.hash("raw-1")).isEmpty(), "해시값 자체는 거절");
        assertTrue(verifier.verify(Provider.APPLE, token, null).isEmpty());
    }

    /** 구글 발급자는 https 유무 두 표기 모두 통과 */
    @Test
    void googleAcceptsBothIssuerForms() throws Exception {
        RSAKey key = new RSAKeyGenerator(2048).keyID("k1").generate();
        var verifier = new IdTokenVerifier(Map.of(Provider.GOOGLE, new IdTokenVerifier.Rule(
                IdTokenVerifier.processor(new ImmutableJWKSet<>(new JWKSet(key.toPublicJWK())), IdTokenVerifier.GOOGLE_ISSUERS, "web"),
                IdTokenVerifier.EmailTrust.CLAIM, IdTokenVerifier.NonceCheck.NONE)));
        for (String issuer : new String[] {"https://accounts.google.com", "accounts.google.com"}) {
            assertTrue(verifier.verify(Provider.GOOGLE, sign(key, claims("web").issuer(issuer).build()), null).isPresent());
        }
        assertTrue(verifier.verify(Provider.GOOGLE, sign(key, claims("web").issuer("https://appleid.apple.com").build()), null).isEmpty());
        assertTrue(verifier.verify(Provider.GOOGLE, sign(key, claims("web").issuer(null).build()), null).isEmpty());
    }

    private static IdTokenVerifier.Rule rule(RSAKey key, String audience, IdTokenVerifier.EmailTrust trust,
            IdTokenVerifier.NonceCheck nonce) {
        return new IdTokenVerifier.Rule(IdTokenVerifier.processor(new ImmutableJWKSet<>(new JWKSet(key.toPublicJWK())),
                Set.of("https://appleid.apple.com"), audience), trust, nonce);
    }

    private static JWTClaimsSet.Builder claims(String audience) {
        return new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com").audience(audience)
                .expirationTime(new Date(System.currentTimeMillis() + 60_000)).claim("email", "e@x.com");
    }

    private static String sign(RSAKey key, JWTClaimsSet claims) throws Exception {
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(key.getKeyID()).build(), claims);
        jwt.sign(new RSASSASigner(key));
        return jwt.serialize();
    }
}
