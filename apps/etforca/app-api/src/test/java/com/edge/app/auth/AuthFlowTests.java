package com.edge.app.auth;

import com.edge.app.ContainerTests;
import com.edge.app.auth.service.AppleTokenService;
import com.edge.app.auth.service.IdTokenVerifier;
import com.edge.app.common.mail.Mailer;
import com.edge.app.member.entity.Provider;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.web.client.RestClient;

import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 계약의 auth 흐름과 PRD 의 게스트 데이터 정책
 * 외부 JWKS 에 의존하는 소셜 검증기의 대체
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class AuthFlowTests extends ContainerTests {
    @LocalServerPort
    int port;
    @Autowired
    JdbcTemplate jdbc;
    @MockitoBean
    IdTokenVerifier idTokenVerifier;
    @MockitoBean
    Mailer mailer;
    @MockitoBean
    AppleTokenService appleTokens;

    @SuppressWarnings("unchecked")
    ResponseEntity<Map> call(String method, String uri, Object body, String... headers) {
        var spec = RestClient.builder().baseUrl("http://localhost:" + port)
                .defaultStatusHandler(s -> true, (r, s) -> {}).build()
                .method(org.springframework.http.HttpMethod.valueOf(method)).uri(uri);
        for (int i = 0; i < headers.length; i += 2) {
            spec.header(headers[i], headers[i + 1]);
        }
        if (body != null) {
            spec.body(body);
        }
        return spec.retrieve().toEntity(Map.class);
    }

    @SuppressWarnings("unchecked")
    Map<String, Object> result(ResponseEntity<Map> res) {
        return (Map<String, Object>) res.getBody().get("result");
    }

    Map<String, Object> signup(String email, String... headers) {
        signupCode(email);
        var res = call("POST", "/api/v1/auth/signup", Map.of("email", email, "password", "pw123456", "nick", "영서", "code", SIGNUP_CODE), headers);
        assertEquals(200, res.getStatusCode().value(), res.getBody().toString());
        return result(res);
    }

    @Test
    void signupThenTokensWorkAndRotate() {
        var auth = signup("a@example.com");
        String bearer = "Bearer " + auth.get("accessToken");
        var me = result(call("GET", "/api/v1/me", null, "Authorization", bearer));
        assertEquals("영서", me.get("nick"));
        assertTrue(((String) me.get("handle")).startsWith("@"));
        assertEquals("a@example.com", me.get("email"));
        assertFalse(me.containsKey("disclaimerAcceptedAt"), "선택 필드는 키 생략");
        assertEquals(false, auth.get("guestMapped"));

        // 새 리프레시 발급과 옛 리프레시의 COMMON400 응답
        var rotated = result(call("POST", "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken"))));
        assertNotEquals(auth.get("refreshToken"), rotated.get("refreshToken"));
        var reused = call("POST", "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken")));
        assertEquals(400, reused.getStatusCode().value());
        assertEquals("COMMON400", reused.getBody().get("code"));

        // 로그아웃 시 회원 리프레시의 전부 폐기
        assertEquals(200, call("POST", "/api/v1/auth/logout", null, "Authorization", "Bearer " + rotated.get("accessToken")).getStatusCode().value());
        assertEquals(400, call("POST", "/api/v1/auth/refresh", Map.of("refreshToken", rotated.get("refreshToken"))).getStatusCode().value());
    }

    @Test
    void loginRejectsWrongPasswordAndDuplicateSignup() {
        signup("b@example.com");
        var bad = call("POST", "/api/v1/auth/login", Map.of("email", "b@example.com", "password", "nope"));
        assertEquals(401, bad.getStatusCode().value());
        assertEquals("AUTH4001", bad.getBody().get("code"));
        assertEquals(401, call("POST", "/api/v1/auth/login", Map.of("email", "nobody@example.com", "password", "x")).getStatusCode().value());
        assertEquals(200, call("POST", "/api/v1/auth/login", Map.of("email", "b@example.com", "password", "pw123456")).getStatusCode().value());
        var dup = call("POST", "/api/v1/auth/signup", Map.of("email", "b@example.com", "password", "pw123456", "nick", "n", "code", SIGNUP_CODE));
        assertEquals(409, dup.getStatusCode().value());
        assertEquals("MEMBER4002", dup.getBody().get("code"));
    }

    String requestSignupCode(String email) {
        assertEquals(200, call("POST", "/api/v1/auth/signup/code", Map.of("email", email)).getStatusCode().value());
        ArgumentCaptor<String> text = ArgumentCaptor.forClass(String.class);
        verify(mailer).send(eq(email), anyString(), text.capture());
        return text.getValue().replaceAll("(?s).*?(\\d{6}).*", "$1");
    }

    ResponseEntity<Map> signupWith(String email, String code) {
        return call("POST", "/api/v1/auth/signup", Map.of("email", email, "password", "pw123456", "nick", "인증", "code", code));
    }

    // 남의 이메일 가입 방지를 위한 메일 수신 코드 필수
    // 5회에서 막히는 코드 대입
    @Test
    void signupRequiresEmailCode() {
        String code = requestSignupCode("v1@example.com");
        String wrong = code.equals("000000") ? "111111" : "000000";
        assertEquals(400, call("POST", "/api/v1/auth/signup", Map.of("email", "v1@example.com", "password", "pw123456", "nick", "인증")).getStatusCode().value());
        var bad = signupWith("v1@example.com", wrong);
        assertEquals(400, bad.getStatusCode().value());
        assertEquals("AUTH4002", bad.getBody().get("code"));
        assertEquals("AUTH4002", signupWith("v1-other@example.com", code).getBody().get("code"));
        assertEquals(200, signupWith("v1@example.com", code).getStatusCode().value());
        assertEquals(0, jdbc.queryForObject("select count(*) from signup_code where email = 'v1@example.com'", Integer.class));

        var taken = call("POST", "/api/v1/auth/signup/code", Map.of("email", "v1@example.com"));
        assertEquals(409, taken.getStatusCode().value());
        assertEquals("MEMBER4002", taken.getBody().get("code"));

        String locked = requestSignupCode("v2@example.com");
        String miss = locked.equals("000000") ? "111111" : "000000";
        for (int i = 0; i < 5; i++) {
            signupWith("v2@example.com", miss);
        }
        assertEquals("AUTH4002", signupWith("v2@example.com", locked).getBody().get("code"));
        assertEquals(200, call("POST", "/api/v1/auth/signup/code", Map.of("email", "v2@example.com")).getStatusCode().value());
        verify(mailer, times(1)).send(eq("v2@example.com"), anyString(), anyString());
    }

    String requestResetCode(String email) {
        assertEquals(200, call("POST", "/api/v1/auth/password-reset", Map.of("email", email)).getStatusCode().value());
        ArgumentCaptor<String> text = ArgumentCaptor.forClass(String.class);
        verify(mailer).send(eq(email), anyString(), text.capture());
        return text.getValue().replaceAll("(?s).*?(\\d{6}).*", "$1");
    }

    ResponseEntity<Map> confirmReset(String email, String code, String newPassword) {
        return call("POST", "/api/v1/auth/password-reset/confirm", Map.of("email", email, "code", code, "newPassword", newPassword));
    }

    // 메일 없이 바로 알리는 미가입 이메일 응답
    // 재요청 폭주의 메일 폭탄 방지
    @Test
    void passwordResetRejectsUnknownEmailAndThrottlesResend() {
        var unknown = call("POST", "/api/v1/auth/password-reset", Map.of("email", "zz@example.com"));
        assertEquals(404, unknown.getStatusCode().value());
        assertEquals("MEMBER4005", unknown.getBody().get("code"));
        verify(mailer, never()).send(eq("zz@example.com"), anyString(), anyString());

        signup("c@example.com");
        requestResetCode("c@example.com");
        assertEquals(200, call("POST", "/api/v1/auth/password-reset", Map.of("email", "c@example.com")).getStatusCode().value());
        verify(mailer, times(1)).send(eq("c@example.com"), anyString(), anyString());
    }

    // 탈취된 세션을 끊는 수단이라 재설정 후 옛 비밀번호와 옛 리프레시의 무효
    @Test
    void passwordResetConfirmChangesPasswordAndRevokesSessions() {
        var auth = signup("r1@example.com");
        String code = requestResetCode("r1@example.com");
        String wrong = code.equals("000000") ? "111111" : "000000";

        var bad = confirmReset("r1@example.com", wrong, "newpw1234");
        assertEquals(400, bad.getStatusCode().value());
        assertEquals("AUTH4002", bad.getBody().get("code"));
        assertEquals("AUTH4002", confirmReset("nobody@example.com", code, "newpw1234").getBody().get("code"));

        assertEquals(200, confirmReset("r1@example.com", code, "newpw1234").getStatusCode().value());
        assertEquals(401, call("POST", "/api/v1/auth/login", Map.of("email", "r1@example.com", "password", "pw123456")).getStatusCode().value());
        assertEquals(200, call("POST", "/api/v1/auth/login", Map.of("email", "r1@example.com", "password", "newpw1234")).getStatusCode().value());
        assertEquals(400, call("POST", "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken"))).getStatusCode().value());
        assertEquals("AUTH4002", confirmReset("r1@example.com", code, "again1234").getBody().get("code"));
    }

    // 시도 제한과 만료 없이는 대입으로 뚫리는 6자리 코드
    @Test
    void passwordResetCodeLocksAfterFiveFailuresAndExpires() {
        signup("r2@example.com");
        String code = requestResetCode("r2@example.com");
        String wrong = code.equals("000000") ? "111111" : "000000";
        for (int i = 0; i < 5; i++) {
            confirmReset("r2@example.com", wrong, "newpw1234");
        }
        assertEquals("AUTH4002", confirmReset("r2@example.com", code, "newpw1234").getBody().get("code"));

        signup("r3@example.com");
        String fresh = requestResetCode("r3@example.com");
        jdbc.update("update password_reset_code set expires_at = now() - interval '1 second' "
                + "where member_id = (select id from member where email = 'r3@example.com')");
        assertEquals("AUTH4002", confirmReset("r3@example.com", fresh, "newpw1234").getBody().get("code"));
    }

    // 동시 오답의 시도 수 덮어쓰기로 인한 5회 제한 초과 대입 방지
    @Test
    void concurrentWrongCodesStillLockAfterFive() throws Exception {
        signup("r4@example.com");
        String code = requestResetCode("r4@example.com");
        String wrong = code.equals("000000") ? "111111" : "000000";
        int n = 10;
        var pool = java.util.concurrent.Executors.newFixedThreadPool(n);
        var gate = new java.util.concurrent.CountDownLatch(1);
        List<java.util.concurrent.Future<?>> futures = new java.util.ArrayList<>();
        for (int i = 0; i < n; i++) {
            futures.add(pool.submit(() -> {
                gate.await();
                return confirmReset("r4@example.com", wrong, "newpw1234");
            }));
        }
        gate.countDown();
        for (var f : futures) {
            f.get();
        }
        pool.shutdown();
        assertEquals(5, jdbc.queryForObject("select attempts from password_reset_code "
                + "where member_id = (select id from member where email = 'r4@example.com')", Integer.class));
        assertEquals("AUTH4002", confirmReset("r4@example.com", code, "newpw1234").getBody().get("code"));
    }

    // 재발송마다 시도 수가 초기화되므로 추측 누적의 상한인 이메일당 하루 발송 수
    @Test
    void codeMailStopsAfterFiveSendsADay() {
        signup("r6@example.com");
        String member = "(select id from member where email = 'r6@example.com')";
        for (int i = 0; i < 5; i++) {
            assertEquals(200, call("POST", "/api/v1/auth/password-reset", Map.of("email", "r6@example.com")).getStatusCode().value());
            jdbc.update("update password_reset_code set created_at = created_at - interval '61 seconds' where member_id = " + member);
        }
        var limited = call("POST", "/api/v1/auth/password-reset", Map.of("email", "r6@example.com"));
        assertEquals(429, limited.getStatusCode().value());
        assertEquals("AUTH4003", limited.getBody().get("code"));
        verify(mailer, times(5)).send(eq("r6@example.com"), anyString(), anyString());
        jdbc.update("update password_reset_code set window_started_at = now() - interval '25 hours' where member_id = " + member);
        assertEquals(200, call("POST", "/api/v1/auth/password-reset", Map.of("email", "r6@example.com")).getStatusCode().value());

        for (int i = 0; i < 5; i++) {
            assertEquals(200, call("POST", "/api/v1/auth/signup/code", Map.of("email", "v3@example.com")).getStatusCode().value());
            jdbc.update("update signup_code set created_at = created_at - interval '61 seconds' where email = 'v3@example.com'");
        }
        assertEquals("AUTH4003", call("POST", "/api/v1/auth/signup/code", Map.of("email", "v3@example.com")).getBody().get("code"));
    }

    // Gmail 하루 한도 소진 시 그날 가입과 재설정이 전부 막히므로 코드 메일의 선차단
    @Test
    void codeMailStopsAtGlobalDailyLimit() {
        LocalDate today = LocalDate.now(ZoneId.of("Asia/Seoul"));
        jdbc.update("insert into mail_daily (day, sent) values (?, 300) on conflict (day) do update set sent = 300", today);
        try {
            var limited = call("POST", "/api/v1/auth/signup/code", Map.of("email", "g1@example.com"));
            assertEquals(429, limited.getStatusCode().value());
            assertEquals("AUTH4003", limited.getBody().get("code"));
            verify(mailer, never()).send(eq("g1@example.com"), anyString(), anyString());
        } finally {
            jdbc.update("delete from mail_daily where day = ?", today);
        }
    }

    // 장난 요청 5번의 정상 코드 무효화를 막기 위한 형식 오류 코드의 시도 수 미차감
    @Test
    void malformedCodeAndShortPasswordAreRejectedBeforeCounting() {
        signup("r5@example.com");
        String code = requestResetCode("r5@example.com");
        for (int i = 0; i < 5; i++) {
            assertEquals("COMMON400", confirmReset("r5@example.com", "abcdef", "newpw1234").getBody().get("code"));
        }
        assertEquals("COMMON400", confirmReset("r5@example.com", code, "short").getBody().get("code"));
        assertEquals(200, confirmReset("r5@example.com", code, "newpw1234").getStatusCode().value());
        signupCode("r7@example.com");
        assertEquals("COMMON400", call("POST", "/api/v1/auth/signup",
                Map.of("email", "r7@example.com", "password", "short", "nick", "n", "code", SIGNUP_CODE)).getBody().get("code"));
    }

    @Test
    void deleteAccountKeepsRowButFreesEmailAndInvalidatesSession() {
        var auth = signup("d@example.com");
        String bearer = "Bearer " + auth.get("accessToken");
        String handle = (String) ((Map<?, ?>) auth.get("me")).get("handle");
        assertEquals(200, call("DELETE", "/api/v1/me", null, "Authorization", bearer).getStatusCode().value());
        // 만료 전 토큰이라도 회원이 없으므로 COMMON401 응답과 리프레시 차단
        assertEquals(401, call("GET", "/api/v1/me", null, "Authorization", bearer).getStatusCode().value());
        assertEquals(400, call("POST", "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken"))).getStatusCode().value());
        // 행은 남기고 식별 정보만 비운 상태의 같은 이메일 재가입 허용
        assertEquals(1, jdbc.queryForObject("select count(*) from member where deleted_at is not null and email is null and handle = ?", Integer.class, handle));
        signup("d@example.com");
    }

    @Test
    void guestDataMovesToAccountOnlyWhenAccountIsEmpty() {
        // 관심 그룹을 가진 디바이스의 직접 시드
        long devicePrincipal = jdbc.queryForObject("insert into device(device_key) values ('dev-1') on conflict (device_key) do update set last_seen_at = now() returning id", Long.class);
        devicePrincipal = jdbc.queryForObject("insert into principal(kind, device_id) values ('device', ?) on conflict (device_id) do update set kind = excluded.kind returning id", Long.class, devicePrincipal);
        long group = jdbc.queryForObject("insert into watch_group(principal_id, key, label, is_default) values (?, 'base', '기본 관심', true) returning id", Long.class, devicePrincipal);
        jdbc.update("insert into watch_item(group_id, etf_code) values (?, '069500')", group);

        var auth = signup("e@example.com", "X-Device-Id", "dev-1");
        assertEquals(true, auth.get("guestMapped"));
        long memberId = jdbc.queryForObject("select id from member where email = 'e@example.com'", Long.class);
        long memberPrincipal = jdbc.queryForObject("select id from principal where member_id = ?", Long.class, memberId);
        assertEquals(1, jdbc.queryForObject("select count(*) from watch_group where principal_id = ?", Integer.class, memberPrincipal));
        assertEquals(0, jdbc.queryForObject("select count(*) from watch_group where principal_id = ?", Integer.class, devicePrincipal));
        assertEquals(memberId, jdbc.queryForObject("select member_id from device where device_key = 'dev-1'", Long.class));

        // 계정에 데이터가 있을 때 두 번째 디바이스 데이터의 이전 생략
        devicePrincipal = jdbc.queryForObject("insert into device(device_key) values ('dev-2') returning id", Long.class);
        devicePrincipal = jdbc.queryForObject("insert into principal(kind, device_id) values ('device', ?) returning id", Long.class, devicePrincipal);
        group = jdbc.queryForObject("insert into watch_group(principal_id, key, label, is_default) values (?, 'base', '기본 관심', true) returning id", Long.class, devicePrincipal);
        jdbc.update("insert into watch_item(group_id, etf_code) values (?, '005930')", group);
        var again = result(call("POST", "/api/v1/auth/login", Map.of("email", "e@example.com", "password", "pw123456"), "X-Device-Id", "dev-2"));
        assertEquals(false, again.get("guestMapped"));
        assertEquals(1, jdbc.queryForObject("select count(*) from watch_group where principal_id = ?", Integer.class, devicePrincipal));
    }

    @Test
    void socialCreatesMemberOnceBySubject() {
        when(idTokenVerifier.verify(eq(Provider.KAKAO), any(), any())).thenReturn(Optional.of(new IdTokenVerifier.Identity("kakao-sub-1", "s@example.com")));
        when(idTokenVerifier.verify(eq(Provider.GOOGLE), any(), any())).thenReturn(Optional.empty());
        var first = result(call("POST", "/api/v1/auth/social", Map.of("provider", "kakao", "idToken", "t", "nonce", "n")));
        var second = result(call("POST", "/api/v1/auth/social", Map.of("provider", "kakao", "idToken", "t", "nonce", "n")));
        assertEquals(1, jdbc.queryForObject("select count(*) from member where provider = 'kakao' and provider_subject = 'kakao-sub-1'", Integer.class));
        assertEquals(((Map<?, ?>) first.get("me")).get("handle"), ((Map<?, ?>) second.get("me")).get("handle"));
        assertEquals(true, first.get("newMember"));
        assertEquals(false, second.get("newMember"));
        var bad = call("POST", "/api/v1/auth/social", Map.of("provider", "google", "idToken", "t"));
        assertEquals(401, bad.getStatusCode().value());
        assertEquals("AUTH4004", bad.getBody().get("code"));
        assertEquals(400, call("POST", "/api/v1/auth/social", Map.of("provider", "email", "idToken", "t")).getStatusCode().value());
        assertEquals(400, call("POST", "/api/v1/auth/social", Map.of("provider", "naver", "idToken", "t")).getStatusCode().value());
    }

    /** 같은 이메일의 다른 가입 방법은 가입·로그인·재설정 대신 그 방법 안내 */
    @Test
    void sameEmailAcrossJoinMethodsAnnouncesTheOriginalMethod() {
        assertEquals(true, signup("mail@example.com").get("newMember"));
        when(idTokenVerifier.verify(eq(Provider.KAKAO), any(), any())).thenReturn(Optional.of(new IdTokenVerifier.Identity("k-mail", "mail@example.com")));
        var kakao = call("POST", "/api/v1/auth/social", Map.of("provider", "kakao", "idToken", "t", "nonce", "n"));
        assertEquals(409, kakao.getStatusCode().value());
        assertEquals("MEMBER4009", kakao.getBody().get("code"));
        assertEquals(0, jdbc.queryForObject("select count(*) from member where provider = 'kakao'", Integer.class));

        when(idTokenVerifier.verify(eq(Provider.GOOGLE), any(), any())).thenReturn(Optional.of(new IdTokenVerifier.Identity("g-1", "g@example.com")));
        assertEquals(200, call("POST", "/api/v1/auth/social", Map.of("provider", "google", "idToken", "t")).getStatusCode().value());
        var login = call("POST", "/api/v1/auth/login", Map.of("email", "g@example.com", "password", "pw123456"));
        assertEquals("MEMBER4007", login.getBody().get("code"));
        assertEquals("MEMBER4007", call("POST", "/api/v1/auth/signup/code", Map.of("email", "g@example.com")).getBody().get("code"));
        assertEquals("MEMBER4007", call("POST", "/api/v1/auth/password-reset", Map.of("email", "g@example.com")).getBody().get("code"));
        assertEquals(false, result(call("POST", "/api/v1/auth/login", Map.of("email", "mail@example.com", "password", "pw123456"))).get("newMember"));
    }

    /** 애플 토큰은 로그인마다 최신 값 보관, 탈퇴 커밋 뒤 철회와 삭제 */
    @Test
    void appleTokenIsStoredOnLoginAndRevokedOnWithdraw() {
        when(idTokenVerifier.verify(eq(Provider.APPLE), any(), any())).thenReturn(Optional.of(new IdTokenVerifier.Identity("a-rt", null)));
        when(appleTokens.exchange("code-1")).thenReturn("rt-1");
        when(appleTokens.exchange("code-2")).thenReturn("rt-2");
        result(call("POST", "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t", "nonce", "n", "authorizationCode", "code-1")));
        var auth = result(call("POST", "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t", "nonce", "n", "authorizationCode", "code-2")));
        assertEquals("rt-2", jdbc.queryForObject("select t.refresh_token from apple_token t join member m on m.id = t.member_id where m.provider_subject = 'a-rt'", String.class));

        assertEquals(200, call("DELETE", "/api/v1/me", null, "Authorization", "Bearer " + auth.get("accessToken")).getStatusCode().value());
        verify(appleTokens).revoke("rt-2");
        assertEquals(0, jdbc.queryForObject("select count(*) from apple_token", Integer.class));
    }

    /** 같은 이메일 안내로 가입이 거절되면 방금 교환한 애플 토큰 철회 */
    @Test
    void rejectedAppleSignupRevokesExchangedToken() {
        signup("taken@example.com");
        when(idTokenVerifier.verify(eq(Provider.APPLE), any(), any())).thenReturn(Optional.of(new IdTokenVerifier.Identity("a-taken", "taken@example.com")));
        when(appleTokens.exchange("code-x")).thenReturn("rt-x");
        var res = call("POST", "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t", "nonce", "n", "authorizationCode", "code-x"));
        assertEquals("MEMBER4009", res.getBody().get("code"));
        verify(appleTokens).revoke("rt-x");
        assertEquals(0, jdbc.queryForObject("select count(*) from apple_token", Integer.class));
    }

    /** 검증된 이메일이 없는 소셜 가입은 이메일 없이 생성 */
    @Test
    void socialWithoutVerifiedEmailStoresNoEmail() {
        when(idTokenVerifier.verify(eq(Provider.APPLE), any(), any())).thenReturn(Optional.of(new IdTokenVerifier.Identity("a-1", null)));
        assertEquals(200, call("POST", "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t")).getStatusCode().value());
        assertEquals(null, jdbc.queryForObject("select email from member where provider_subject = 'a-1'", String.class));
    }

    /** 동시 가입 4건이 선검사를 모두 통과해 유니크 제약에서 500 이 나던 문제의 재발 방지 */
    @Test
    void concurrentSignupsWithSameEmailYieldOneAccountAndConflictsForTheRest() throws Exception {
        int n = 6;
        var pool = java.util.concurrent.Executors.newFixedThreadPool(n);
        var gate = new java.util.concurrent.CountDownLatch(1);
        List<java.util.concurrent.Future<Integer>> futures = new java.util.ArrayList<>();
        signupCode("race@example.com");
        for (int i = 0; i < n; i++) {
            futures.add(pool.submit(() -> {
                gate.await();
                return call("POST", "/api/v1/auth/signup", Map.of("email", "race@example.com", "password", "pw123456", "nick", "n", "code", SIGNUP_CODE))
                        .getStatusCode().value();
            }));
        }
        gate.countDown();
        List<Integer> codes = new java.util.ArrayList<>();
        for (var f : futures) {
            codes.add(f.get());
        }
        pool.shutdown();
        assertEquals(1, codes.stream().filter(c -> c == 200).count(), codes.toString());
        assertEquals(n - 1, codes.stream().filter(c -> c == 409).count(), codes.toString());
    }
}
