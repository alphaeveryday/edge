-- 비밀번호 재설정 6자리 코드. 회원당 1행, 재요청 시 덮어쓰기.
CREATE TABLE password_reset_code (
    member_id   BIGINT       PRIMARY KEY,
    code_hash   VARCHAR(64)  NOT NULL,
    attempts    SMALLINT     NOT NULL DEFAULT 0,
    expires_at  TIMESTAMPTZ  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
