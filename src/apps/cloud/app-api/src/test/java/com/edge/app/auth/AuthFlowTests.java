package com.edge.app.auth;

import com.edge.app.ContainerTests;
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

/** 계약의 auth 흐름과 PRD 게스트 데이터 정책. 소셜 검증기는 외부 JWKS 라 대체한다. */
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
        var res = call("POST", "/api/v1/auth/signup", Map.of("email", email, "password", "pw123456", "nick", "영서"), headers);
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

        // 회전: 새 리프레시가 나오고 옛 것은 COMMON400.
        var rotated = result(call("POST", "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken"))));
        assertNotEquals(auth.get("refreshToken"), rotated.get("refreshToken"));
        var reused = call("POST", "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken")));
        assertEquals(400, reused.getStatusCode().value());
        assertEquals("COMMON400", reused.getBody().get("code"));

        // 로그아웃은 회원의 리프레시를 전부 폐기한다.
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
        var dup = call("POST", "/api/v1/auth/signup", Map.of("email", "b@example.com", "password", "x", "nick", "n"));
        assertEquals(409, dup.getStatusCode().value());
        assertEquals("MEMBER4002", dup.getBody().get("code"));
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

    // 미가입 이메일은 메일 없이 바로 알려 주고, 재요청 폭주가 메일 폭탄이 되지 않아야 한다
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

    // 재설정은 탈취된 세션을 끊는 수단이라 옛 비밀번호와 옛 리프레시가 모두 무효여야 한다
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

    // 6자리 코드는 시도 제한과 만료가 없으면 대입으로 뚫린다
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

    @Test
    void deleteAccountKeepsRowButFreesEmailAndInvalidatesSession() {
        var auth = signup("d@example.com");
        String bearer = "Bearer " + auth.get("accessToken");
        String handle = (String) ((Map<?, ?>) auth.get("me")).get("handle");
        assertEquals(200, call("DELETE", "/api/v1/me", null, "Authorization", bearer).getStatusCode().value());
        // 토큰은 만료 전이지만 회원이 없으므로 COMMON401, 리프레시도 막힌다.
        assertEquals(401, call("GET", "/api/v1/me", null, "Authorization", bearer).getStatusCode().value());
        assertEquals(400, call("POST", "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken"))).getStatusCode().value());
        // 행은 남고 식별 정보만 비어 같은 이메일로 재가입할 수 있다.
        assertEquals(1, jdbc.queryForObject("select count(*) from member where deleted_at is not null and email is null and handle = ?", Integer.class, handle));
        signup("d@example.com");
    }

    @Test
    void guestDataMovesToAccountOnlyWhenAccountIsEmpty() {
        // 디바이스가 관심 그룹을 갖고 있다(관심 도메인 구현 전이라 직접 심는다).
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

        // 계정에 이미 데이터가 있으면 두 번째 디바이스의 것은 옮기지 않는다.
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
        when(idTokenVerifier.verify(eq(Provider.APPLE), any())).thenReturn(Optional.of(new IdTokenVerifier.Identity("apple-sub-1", "s@example.com")));
        when(idTokenVerifier.verify(eq(Provider.GOOGLE), any())).thenReturn(Optional.empty());
        var first = result(call("POST", "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t")));
        var second = result(call("POST", "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t")));
        assertEquals(1, jdbc.queryForObject("select count(*) from member where provider_subject = 'apple-sub-1'", Integer.class));
        assertEquals(((Map<?, ?>) first.get("me")).get("handle"), ((Map<?, ?>) second.get("me")).get("handle"));
        var bad = call("POST", "/api/v1/auth/social", Map.of("provider", "google", "idToken", "t"));
        assertEquals(400, bad.getStatusCode().value());
        assertEquals(400, call("POST", "/api/v1/auth/social", Map.of("provider", "email", "idToken", "t")).getStatusCode().value());
        assertEquals(400, call("POST", "/api/v1/auth/social", Map.of("provider", "kakao", "idToken", "t")).getStatusCode().value());
    }

    /** 서버 로그(2026-09-28): 앱이 가입을 4건 동시에 보내 선검사를 전부 통과하고 uq_member_email 에서 500 이 났다. */
    @Test
    void concurrentSignupsWithSameEmailYieldOneAccountAndConflictsForTheRest() throws Exception {
        int n = 6;
        var pool = java.util.concurrent.Executors.newFixedThreadPool(n);
        var gate = new java.util.concurrent.CountDownLatch(1);
        List<java.util.concurrent.Future<Integer>> futures = new java.util.ArrayList<>();
        for (int i = 0; i < n; i++) {
            futures.add(pool.submit(() -> {
                gate.await();
                return call("POST", "/api/v1/auth/signup", Map.of("email", "race@example.com", "password", "pw123456", "nick", "n"))
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
