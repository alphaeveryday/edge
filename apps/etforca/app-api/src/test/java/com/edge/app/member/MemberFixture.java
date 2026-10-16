package com.edge.app.member;

import com.edge.app.auth.dto.SignupRequest;
import org.instancio.Instancio;
import org.instancio.Select;
import org.springframework.jdbc.core.JdbcTemplate;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

public class MemberFixture {
    public static final String SIGNUP_CODE = "000000";

    public static SignupRequest createSignupRequest() {
        return Instancio.of(SignupRequest.class)
                .set(Select.field(SignupRequest::email), Instancio.gen().net().email().get())
                .set(Select.field(SignupRequest::password), "pw123456")
                .set(Select.field(SignupRequest::nick), "n")
                .set(Select.field(SignupRequest::code), SIGNUP_CODE)
                .create();
    }

    // 메일 발송 없이 가입 가능하도록 DB 에 인증 코드를 심음
    public static void plantSignupCode(JdbcTemplate jdbc, String email) {
        jdbc.update("insert into signup_code(email, code_hash, expires_at) values (?, ?, now() + interval '10 minutes') "
                + "on conflict (email) do update set code_hash = excluded.code_hash, attempts = 0, expires_at = excluded.expires_at",
                email, sha256(SIGNUP_CODE));
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes()));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
