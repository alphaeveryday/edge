package com.edge.app.adapter.webapi.auth;

import com.edge.app.application.auth.required.AppleTokenClient;
import com.edge.app.application.auth.required.SocialIdentityVerifier;
import com.edge.app.application.mail.required.EmailSender;
import com.edge.app.domain.member.MemberFixture;
import com.edge.app.domain.member.Provider;
import com.edge.app.support.stereotype.WebApiAdapterTest;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.assertj.MockMvcTester;
import org.springframework.test.web.servlet.assertj.MockMvcTester.MockMvcRequestBuilder;
import org.springframework.test.web.servlet.assertj.MvcTestResult;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static com.edge.app.domain.member.MemberFixture.SIGNUP_CODE;
import static com.edge.app.support.JsonResults.result;
import static org.assertj.core.api.Assertions.assertThat;
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
@WebApiAdapterTest
@MockitoBean(types = {SocialIdentityVerifier.class, EmailSender.class, AppleTokenClient.class})
record AuthApiTest(MockMvcTester mvcTester, ObjectMapper objectMapper, JdbcTemplate jdbc,
        SocialIdentityVerifier idTokenVerifier, EmailSender mailer, AppleTokenClient appleTokens) {
    @Test
    void signupThenTokensWorkAndRotate() {
        var auth = signup("a@example.com");
        String bearer = "Bearer " + auth.get("accessToken");
        Map<String, Object> me = result(call(HttpMethod.GET, "/api/v1/me", null, "Authorization", bearer));
        assertThat(me.get("nick")).isEqualTo("영서");
        assertThat((String) me.get("handle")).startsWith("@");
        assertThat(me.get("email")).isEqualTo("a@example.com");
        assertThat(me).as("선택 필드는 키 생략").doesNotContainKey("disclaimerAcceptedAt");
        assertThat(auth.get("guestMapped")).isEqualTo(false);

        // 새 리프레시 발급과 옛 리프레시의 COMMON400 응답
        Map<String, Object> rotated = result(call(HttpMethod.POST, "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken"))));
        assertThat(rotated.get("refreshToken")).isNotEqualTo(auth.get("refreshToken"));
        assertThat(call(HttpMethod.POST, "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken"))))
                .hasStatus(400)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("COMMON400");

        // 로그아웃 시 회원 리프레시의 전부 폐기
        assertThat(call(HttpMethod.POST, "/api/v1/auth/logout", null, "Authorization", "Bearer " + rotated.get("accessToken"))).hasStatusOk();
        assertThat(call(HttpMethod.POST, "/api/v1/auth/refresh", Map.of("refreshToken", rotated.get("refreshToken")))).hasStatus(400);
    }

    @Test
    void loginRejectsWrongPasswordAndDuplicateSignup() {
        signup("b@example.com");
        assertThat(login("b@example.com", "nope")).hasStatus(401).bodyJson().extractingPath("$.code").isEqualTo("AUTH4001");
        assertThat(login("nobody@example.com", "x")).hasStatus(401);
        assertThat(login("b@example.com", "pw123456")).hasStatusOk();
        assertThat(call(HttpMethod.POST, "/api/v1/auth/signup", Map.of("email", "b@example.com", "password", "pw123456", "nick", "n", "code", SIGNUP_CODE)))
                .hasStatus(409)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("MEMBER4002");
    }

    // 남의 이메일 가입 방지를 위한 메일 수신 코드 필수
    // 5회에서 막히는 코드 대입
    @Test
    void signupRequiresEmailCode() {
        String code = requestSignupCode("v1@example.com");
        String wrong = code.equals("000000") ? "111111" : "000000";
        assertThat(call(HttpMethod.POST, "/api/v1/auth/signup", Map.of("email", "v1@example.com", "password", "pw123456", "nick", "인증")))
                .hasStatus(400);
        assertThat(signupWith("v1@example.com", wrong)).hasStatus(400).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");
        assertThat(signupWith("v1-other@example.com", code)).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");
        assertThat(signupWith("v1@example.com", code)).hasStatusOk();
        assertThat(jdbc.queryForObject("select count(*) from signup_code where email = 'v1@example.com'", Integer.class)).isEqualTo(0);

        assertThat(call(HttpMethod.POST, "/api/v1/auth/signup/code", Map.of("email", "v1@example.com")))
                .hasStatus(409)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("MEMBER4002");

        String locked = requestSignupCode("v2@example.com");
        String miss = locked.equals("000000") ? "111111" : "000000";
        for (int i = 0; i < 5; i++) {
            signupWith("v2@example.com", miss);
        }
        assertThat(signupWith("v2@example.com", locked)).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");
        assertThat(call(HttpMethod.POST, "/api/v1/auth/signup/code", Map.of("email", "v2@example.com"))).hasStatusOk();
        verify(mailer, times(1)).send(eq("v2@example.com"), anyString(), anyString());
    }

    // 메일 없이 바로 알리는 미가입 이메일 응답
    // 재요청 폭주의 메일 폭탄 방지
    @Test
    void passwordResetRejectsUnknownEmailAndThrottlesResend() {
        assertThat(call(HttpMethod.POST, "/api/v1/auth/password-reset", Map.of("email", "zz@example.com")))
                .hasStatus(404)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("MEMBER4005");
        verify(mailer, never()).send(eq("zz@example.com"), anyString(), anyString());

        signup("c@example.com");
        requestResetCode("c@example.com");
        assertThat(call(HttpMethod.POST, "/api/v1/auth/password-reset", Map.of("email", "c@example.com"))).hasStatusOk();
        verify(mailer, times(1)).send(eq("c@example.com"), anyString(), anyString());
    }

    // 탈취된 세션을 끊는 수단이라 재설정 후 옛 비밀번호와 옛 리프레시의 무효
    @Test
    void passwordResetConfirmChangesPasswordAndRevokesSessions() {
        var auth = signup("r1@example.com");
        String code = requestResetCode("r1@example.com");
        String wrong = code.equals("000000") ? "111111" : "000000";

        assertThat(confirmReset("r1@example.com", wrong, "newpw1234")).hasStatus(400).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");
        assertThat(confirmReset("nobody@example.com", code, "newpw1234")).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");

        assertThat(confirmReset("r1@example.com", code, "newpw1234")).hasStatusOk();
        assertThat(login("r1@example.com", "pw123456")).hasStatus(401);
        assertThat(login("r1@example.com", "newpw1234")).hasStatusOk();
        assertThat(call(HttpMethod.POST, "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken")))).hasStatus(400);
        assertThat(confirmReset("r1@example.com", code, "again1234")).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");
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
        assertThat(confirmReset("r2@example.com", code, "newpw1234")).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");

        signup("r3@example.com");
        String fresh = requestResetCode("r3@example.com");
        jdbc.update("update password_reset_code set expires_at = now() - interval '1 second' "
                + "where member_id = (select id from member where email = 'r3@example.com')");
        assertThat(confirmReset("r3@example.com", fresh, "newpw1234")).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");
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
        assertThat(jdbc.queryForObject("select attempts from password_reset_code "
                + "where member_id = (select id from member where email = 'r4@example.com')", Integer.class)).isEqualTo(5);
        assertThat(confirmReset("r4@example.com", code, "newpw1234")).bodyJson().extractingPath("$.code").isEqualTo("AUTH4002");
    }

    // 재발송마다 시도 수가 초기화되므로 추측 누적의 상한인 이메일당 하루 발송 수
    @Test
    void codeMailStopsAfterFiveSendsADay() {
        signup("r6@example.com");
        String member = "(select id from member where email = 'r6@example.com')";
        for (int i = 0; i < 5; i++) {
            assertThat(call(HttpMethod.POST, "/api/v1/auth/password-reset", Map.of("email", "r6@example.com"))).hasStatusOk();
            jdbc.update("update password_reset_code set created_at = created_at - interval '61 seconds' where member_id = " + member);
        }
        assertThat(call(HttpMethod.POST, "/api/v1/auth/password-reset", Map.of("email", "r6@example.com")))
                .hasStatus(429)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("AUTH4003");
        verify(mailer, times(5)).send(eq("r6@example.com"), anyString(), anyString());
        jdbc.update("update password_reset_code set window_started_at = now() - interval '25 hours' where member_id = " + member);
        assertThat(call(HttpMethod.POST, "/api/v1/auth/password-reset", Map.of("email", "r6@example.com"))).hasStatusOk();

        for (int i = 0; i < 5; i++) {
            assertThat(call(HttpMethod.POST, "/api/v1/auth/signup/code", Map.of("email", "v3@example.com"))).hasStatusOk();
            jdbc.update("update signup_code set created_at = created_at - interval '61 seconds' where email = 'v3@example.com'");
        }
        assertThat(call(HttpMethod.POST, "/api/v1/auth/signup/code", Map.of("email", "v3@example.com")))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("AUTH4003");
    }

    // Gmail 하루 한도 소진 시 그날 가입과 재설정이 전부 막히므로 코드 메일의 선차단
    @Test
    void codeMailStopsAtGlobalDailyLimit() {
        LocalDate today = LocalDate.now(ZoneId.of("Asia/Seoul"));
        jdbc.update("insert into mail_daily (day, sent) values (?, 300) on conflict (day) do update set sent = 300", today);
        try {
            assertThat(call(HttpMethod.POST, "/api/v1/auth/signup/code", Map.of("email", "g1@example.com")))
                    .hasStatus(429)
                    .bodyJson()
                    .extractingPath("$.code").isEqualTo("AUTH4003");
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
            assertThat(confirmReset("r5@example.com", "abcdef", "newpw1234")).bodyJson().extractingPath("$.code").isEqualTo("COMMON400");
        }
        assertThat(confirmReset("r5@example.com", code, "short")).bodyJson().extractingPath("$.code").isEqualTo("COMMON400");
        assertThat(confirmReset("r5@example.com", code, "newpw1234")).hasStatusOk();
        MemberFixture.plantSignupCode(jdbc, "r7@example.com");
        assertThat(call(HttpMethod.POST, "/api/v1/auth/signup", Map.of("email", "r7@example.com", "password", "short", "nick", "n", "code", SIGNUP_CODE)))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("COMMON400");
    }

    @Test
    void deleteAccountKeepsRowButFreesEmailAndInvalidatesSession() {
        var auth = signup("d@example.com");
        String bearer = "Bearer " + auth.get("accessToken");
        String handle = (String) ((Map<?, ?>) auth.get("me")).get("handle");
        assertThat(call(HttpMethod.DELETE, "/api/v1/me", null, "Authorization", bearer)).hasStatusOk();
        // 만료 전 토큰이라도 회원이 없으므로 COMMON401 응답과 리프레시 차단
        assertThat(call(HttpMethod.GET, "/api/v1/me", null, "Authorization", bearer)).hasStatus(401);
        assertThat(call(HttpMethod.POST, "/api/v1/auth/refresh", Map.of("refreshToken", auth.get("refreshToken")))).hasStatus(400);
        // 행은 남기고 식별 정보만 비운 상태의 같은 이메일 재가입 허용
        assertThat(jdbc.queryForObject("select count(*) from member where deleted_at is not null and email is null and handle = ?", Integer.class, handle))
                .isEqualTo(1);
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
        assertThat(auth.get("guestMapped")).isEqualTo(true);
        long memberId = jdbc.queryForObject("select id from member where email = 'e@example.com'", Long.class);
        long memberPrincipal = jdbc.queryForObject("select id from principal where member_id = ?", Long.class, memberId);
        assertThat(jdbc.queryForObject("select count(*) from watch_group where principal_id = ?", Integer.class, memberPrincipal)).isEqualTo(1);
        assertThat(jdbc.queryForObject("select count(*) from watch_group where principal_id = ?", Integer.class, devicePrincipal)).isEqualTo(0);
        assertThat(jdbc.queryForObject("select member_id from device where device_key = 'dev-1'", Long.class)).isEqualTo(memberId);

        // 계정에 데이터가 있을 때 두 번째 디바이스 데이터의 이전 생략
        devicePrincipal = jdbc.queryForObject("insert into device(device_key) values ('dev-2') returning id", Long.class);
        devicePrincipal = jdbc.queryForObject("insert into principal(kind, device_id) values ('device', ?) returning id", Long.class, devicePrincipal);
        group = jdbc.queryForObject("insert into watch_group(principal_id, key, label, is_default) values (?, 'base', '기본 관심', true) returning id", Long.class, devicePrincipal);
        jdbc.update("insert into watch_item(group_id, etf_code) values (?, '005930')", group);
        Map<String, Object> again = result(call(HttpMethod.POST, "/api/v1/auth/login", Map.of("email", "e@example.com", "password", "pw123456"), "X-Device-Id", "dev-2"));
        assertThat(again.get("guestMapped")).isEqualTo(false);
        assertThat(jdbc.queryForObject("select count(*) from watch_group where principal_id = ?", Integer.class, devicePrincipal)).isEqualTo(1);
    }

    @Test
    void socialCreatesMemberOnceBySubject() {
        when(idTokenVerifier.verify(eq(Provider.KAKAO), any(), any())).thenReturn(Optional.of(new SocialIdentityVerifier.Identity("kakao-sub-1", "s@example.com")));
        when(idTokenVerifier.verify(eq(Provider.GOOGLE), any(), any())).thenReturn(Optional.empty());
        Map<String, Object> first = result(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "kakao", "idToken", "t", "nonce", "n")));
        Map<String, Object> second = result(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "kakao", "idToken", "t", "nonce", "n")));
        assertThat(jdbc.queryForObject("select count(*) from member where provider = 'kakao' and provider_subject = 'kakao-sub-1'", Integer.class)).isEqualTo(1);
        assertThat(((Map<?, ?>) second.get("me")).get("handle")).isEqualTo(((Map<?, ?>) first.get("me")).get("handle"));
        assertThat(first.get("newMember")).isEqualTo(true);
        assertThat(second.get("newMember")).isEqualTo(false);
        assertThat(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "google", "idToken", "t")))
                .hasStatus(401)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("AUTH4004");
        assertThat(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "email", "idToken", "t"))).hasStatus(400);
        assertThat(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "naver", "idToken", "t"))).hasStatus(400);
    }

    /** 같은 이메일의 다른 가입 방법은 가입·로그인·재설정 대신 그 방법 안내 */
    @Test
    void sameEmailAcrossJoinMethodsAnnouncesTheOriginalMethod() {
        assertThat(signup("mail@example.com").get("newMember")).isEqualTo(true);
        when(idTokenVerifier.verify(eq(Provider.KAKAO), any(), any())).thenReturn(Optional.of(new SocialIdentityVerifier.Identity("k-mail", "mail@example.com")));
        assertThat(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "kakao", "idToken", "t", "nonce", "n")))
                .hasStatus(409)
                .bodyJson()
                .extractingPath("$.code").isEqualTo("MEMBER4009");
        assertThat(jdbc.queryForObject("select count(*) from member where provider = 'kakao'", Integer.class)).isEqualTo(0);

        when(idTokenVerifier.verify(eq(Provider.GOOGLE), any(), any())).thenReturn(Optional.of(new SocialIdentityVerifier.Identity("g-1", "g@example.com")));
        assertThat(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "google", "idToken", "t"))).hasStatusOk();
        assertThat(login("g@example.com", "pw123456")).bodyJson().extractingPath("$.code").isEqualTo("MEMBER4007");
        assertThat(call(HttpMethod.POST, "/api/v1/auth/signup/code", Map.of("email", "g@example.com")))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("MEMBER4007");
        assertThat(call(HttpMethod.POST, "/api/v1/auth/password-reset", Map.of("email", "g@example.com")))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("MEMBER4007");
        assertThat(login("mail@example.com", "pw123456")).hasStatus2xxSuccessful().bodyJson().extractingPath("$.result.newMember").isEqualTo(false);
    }

    /** 애플 토큰은 로그인마다 최신 값 보관, 탈퇴 커밋 뒤 철회와 삭제 */
    @Test
    void appleTokenIsStoredOnLoginAndRevokedOnWithdraw() {
        when(idTokenVerifier.verify(eq(Provider.APPLE), any(), any())).thenReturn(Optional.of(new SocialIdentityVerifier.Identity("a-rt", null)));
        when(appleTokens.exchange("code-1")).thenReturn("rt-1");
        when(appleTokens.exchange("code-2")).thenReturn("rt-2");
        assertThat(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t", "nonce", "n", "authorizationCode", "code-1")))
                .hasStatus2xxSuccessful();
        Map<String, Object> auth = result(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t", "nonce", "n", "authorizationCode", "code-2")));
        assertThat(jdbc.queryForObject("select t.refresh_token from apple_token t join member m on m.id = t.member_id where m.provider_subject = 'a-rt'", String.class))
                .isEqualTo("rt-2");

        assertThat(call(HttpMethod.DELETE, "/api/v1/me", null, "Authorization", "Bearer " + auth.get("accessToken"))).hasStatusOk();
        verify(appleTokens).revoke("rt-2");
        assertThat(jdbc.queryForObject("select count(*) from apple_token", Integer.class)).isEqualTo(0);
    }

    /** 같은 이메일 안내로 가입이 거절되면 방금 교환한 애플 토큰 철회 */
    @Test
    void rejectedAppleSignupRevokesExchangedToken() {
        signup("taken@example.com");
        when(idTokenVerifier.verify(eq(Provider.APPLE), any(), any())).thenReturn(Optional.of(new SocialIdentityVerifier.Identity("a-taken", "taken@example.com")));
        when(appleTokens.exchange("code-x")).thenReturn("rt-x");
        assertThat(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t", "nonce", "n", "authorizationCode", "code-x")))
                .bodyJson()
                .extractingPath("$.code").isEqualTo("MEMBER4009");
        verify(appleTokens).revoke("rt-x");
        assertThat(jdbc.queryForObject("select count(*) from apple_token", Integer.class)).isEqualTo(0);
    }

    /** 검증된 이메일이 없는 소셜 가입은 이메일 없이 생성 */
    @Test
    void socialWithoutVerifiedEmailStoresNoEmail() {
        when(idTokenVerifier.verify(eq(Provider.APPLE), any(), any())).thenReturn(Optional.of(new SocialIdentityVerifier.Identity("a-1", null)));
        assertThat(call(HttpMethod.POST, "/api/v1/auth/social", Map.of("provider", "apple", "idToken", "t"))).hasStatusOk();
        assertThat(jdbc.queryForObject("select email from member where provider_subject = 'a-1'", String.class)).isNull();
    }

    /** 동시 가입 4건이 선검사를 모두 통과해 유니크 제약에서 500 이 나던 문제의 재발 방지 */
    @Test
    void concurrentSignupsWithSameEmailYieldOneAccountAndConflictsForTheRest() throws Exception {
        int n = 6;
        var pool = java.util.concurrent.Executors.newFixedThreadPool(n);
        var gate = new java.util.concurrent.CountDownLatch(1);
        List<java.util.concurrent.Future<Integer>> futures = new java.util.ArrayList<>();
        MemberFixture.plantSignupCode(jdbc, "race@example.com");
        for (int i = 0; i < n; i++) {
            futures.add(pool.submit(() -> {
                gate.await();
                return call(HttpMethod.POST, "/api/v1/auth/signup", Map.of("email", "race@example.com", "password", "pw123456", "nick", "n", "code", SIGNUP_CODE))
                        .getResponse().getStatus();
            }));
        }
        gate.countDown();
        List<Integer> codes = new java.util.ArrayList<>();
        for (var f : futures) {
            codes.add(f.get());
        }
        pool.shutdown();
        assertThat(codes.stream().filter(c -> c == 200).count()).as(codes.toString()).isEqualTo(1);
        assertThat(codes.stream().filter(c -> c == 409).count()).as(codes.toString()).isEqualTo(n - 1);
    }

    private MvcTestResult call(HttpMethod method, String uri, Object body, String... headers) {
        MockMvcRequestBuilder request = mvcTester.method(method).uri(uri);
        for (int i = 0; i < headers.length; i += 2) {
            request.header(headers[i], headers[i + 1]);
        }
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).content(objectMapper.writeValueAsString(body));
        }
        return request.exchange();
    }

    private Map<String, Object> signup(String email, String... headers) {
        MemberFixture.plantSignupCode(jdbc, email);
        MvcTestResult res = call(HttpMethod.POST, "/api/v1/auth/signup", Map.of("email", email, "password", "pw123456", "nick", "영서", "code", SIGNUP_CODE), headers);
        assertThat(res).hasStatusOk();
        return result(res);
    }

    private MvcTestResult login(String email, String password) {
        return call(HttpMethod.POST, "/api/v1/auth/login", Map.of("email", email, "password", password));
    }

    private String requestSignupCode(String email) {
        assertThat(call(HttpMethod.POST, "/api/v1/auth/signup/code", Map.of("email", email))).hasStatusOk();
        ArgumentCaptor<String> text = ArgumentCaptor.forClass(String.class);
        verify(mailer).send(eq(email), anyString(), text.capture());
        return text.getValue().replaceAll("(?s).*?(\\d{6}).*", "$1");
    }

    private MvcTestResult signupWith(String email, String code) {
        return call(HttpMethod.POST, "/api/v1/auth/signup", Map.of("email", email, "password", "pw123456", "nick", "인증", "code", code));
    }

    private String requestResetCode(String email) {
        assertThat(call(HttpMethod.POST, "/api/v1/auth/password-reset", Map.of("email", email))).hasStatusOk();
        ArgumentCaptor<String> text = ArgumentCaptor.forClass(String.class);
        verify(mailer).send(eq(email), anyString(), text.capture());
        return text.getValue().replaceAll("(?s).*?(\\d{6}).*", "$1");
    }

    private MvcTestResult confirmReset(String email, String code, String newPassword) {
        return call(HttpMethod.POST, "/api/v1/auth/password-reset/confirm", Map.of("email", email, "code", code, "newPassword", newPassword));
    }
}
