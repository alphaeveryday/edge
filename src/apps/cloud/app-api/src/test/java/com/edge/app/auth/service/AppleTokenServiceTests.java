package com.edge.app.auth.service;

import com.nimbusds.jose.crypto.ECDSAVerifier;
import com.nimbusds.jwt.SignedJWT;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.interfaces.ECPublicKey;
import java.security.spec.ECGenParameterSpec;
import java.time.Instant;
import java.util.Base64;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

/** 애플 토큰 교환·철회 요청 형식과 키 미설정·애플 오류의 무해한 실패 */
class AppleTokenServiceTests {
    private final KeyPair key = ecKey();
    private final RestClient.Builder builder = RestClient.builder().baseUrl("https://appleid.apple.com");
    private final MockRestServiceServer apple = MockRestServiceServer.bindTo(builder).build();

    @Test
    void exchangesCodeAndRevokesWithSignedClientSecret() throws Exception {
        var service = new AppleTokenService(properties(pem(key)), builder.build());
        apple.expect(requestTo("https://appleid.apple.com/auth/token")).andExpect(method(HttpMethod.POST))
                .andExpect(content().formDataContains(Map.of("client_id", "bundle", "code", "c1", "grant_type", "authorization_code")))
                .andRespond(withSuccess("{\"refresh_token\":\"rt-1\"}", MediaType.APPLICATION_JSON));
        apple.expect(requestTo("https://appleid.apple.com/auth/revoke"))
                .andExpect(content().formDataContains(Map.of("token", "rt-1", "token_type_hint", "refresh_token")))
                .andRespond(withSuccess());

        assertEquals("rt-1", service.exchange("c1"));
        service.revoke("rt-1");
        apple.verify();

        SignedJWT secret = SignedJWT.parse(service.clientSecret(Instant.now()));
        assertTrue(secret.verify(new ECDSAVerifier((ECPublicKey) key.getPublic())));
        assertEquals("KEY", secret.getHeader().getKeyID());
        assertEquals("TEAM", secret.getJWTClaimsSet().getIssuer());
        assertEquals("bundle", secret.getJWTClaimsSet().getSubject());
        assertEquals(List.of("https://appleid.apple.com"), secret.getJWTClaimsSet().getAudience());
    }

    @Test
    void appleErrorYieldsNoTokenAndUnconfiguredKeySkipsCalls() {
        var service = new AppleTokenService(properties(pem(key)), builder.build());
        apple.expect(requestTo("https://appleid.apple.com/auth/token")).andRespond(withStatus(HttpStatus.BAD_REQUEST));
        assertNull(service.exchange("bad"));
        apple.verify();

        var unconfigured = new AppleTokenService(properties(""), RestClient.builder().build());
        assertNull(unconfigured.exchange("c1"));
        unconfigured.revoke("rt");
    }

    private static SocialProperties properties(String privateKey) {
        return new SocialProperties("bundle", null, null, "TEAM", "KEY", privateKey);
    }

    // 환경 변수처럼 줄바꿈을 \n 문자열로 담은 PKCS#8 본문
    private static String pem(KeyPair key) {
        String body = Base64.getEncoder().encodeToString(key.getPrivate().getEncoded());
        return body.substring(0, 40) + "\\n" + body.substring(40);
    }

    private static KeyPair ecKey() {
        try {
            KeyPairGenerator generator = KeyPairGenerator.getInstance("EC");
            generator.initialize(new ECGenParameterSpec("secp256r1"));
            return generator.generateKeyPair();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
