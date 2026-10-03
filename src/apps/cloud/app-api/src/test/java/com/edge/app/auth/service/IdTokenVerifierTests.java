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

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 서명, 발급자, audience, 만료 중 하나라도 어긋난 토큰의 로그인 거부 */
class IdTokenVerifierTests {
    @Test
    void acceptsOnlyTokensSignedByIssuerForOurAudience() throws Exception {
        RSAKey key = new RSAKeyGenerator(2048).keyID("k1").generate();
        RSAKey other = new RSAKeyGenerator(2048).keyID("k2").generate();
        var verifier = new IdTokenVerifier(Map.of(Provider.APPLE, IdTokenVerifier.processor(
                new ImmutableJWKSet<>(new JWKSet(key.toPublicJWK())), "https://appleid.apple.com", "com.edge.orca")));
        Date future = new Date(System.currentTimeMillis() + 60_000);

        String good = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("com.edge.orca").expirationTime(future).claim("email", "e@x.com").build());
        assertEquals(new IdTokenVerifier.Identity("s1", "e@x.com"), verifier.verify(Provider.APPLE, good).orElseThrow());

        String wrongKey = sign(other, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("com.edge.orca").expirationTime(future).build());
        String wrongAud = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("someone.else").expirationTime(future).build());
        String wrongIss = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://accounts.google.com")
                .audience("com.edge.orca").expirationTime(future).build());
        String expired = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("com.edge.orca").expirationTime(new Date(0)).build());
        for (String bad : new String[] {wrongKey, wrongAud, wrongIss, expired, "garbage"}) {
            assertTrue(verifier.verify(Provider.APPLE, bad).isEmpty());
        }
        assertTrue(verifier.verify(Provider.GOOGLE, good).isEmpty(), "설정 없는 provider 는 거절");
    }

    @Test
    void unconfiguredAudienceRejectsEverything() throws Exception {
        RSAKey key = new RSAKeyGenerator(2048).keyID("k1").generate();
        var verifier = new IdTokenVerifier(Map.of(Provider.APPLE, IdTokenVerifier.processor(
                new ImmutableJWKSet<>(new JWKSet(key.toPublicJWK())), "https://appleid.apple.com", "")));
        String token = sign(key, new JWTClaimsSet.Builder().subject("s1").issuer("https://appleid.apple.com")
                .audience("com.edge.orca").expirationTime(new Date(System.currentTimeMillis() + 60_000)).build());
        assertTrue(verifier.verify(Provider.APPLE, token).isEmpty());
    }

    private static String sign(RSAKey key, JWTClaimsSet claims) throws Exception {
        SignedJWT jwt = new SignedJWT(new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(key.getKeyID()).build(), claims);
        jwt.sign(new RSASSASigner(key));
        return jwt.serialize();
    }
}
