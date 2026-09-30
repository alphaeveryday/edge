-- 가입 이메일 인증 6자리 코드. 이메일당 1행, 재요청 시 덮어쓰기, 가입 성공 시 삭제.
CREATE TABLE signup_code (
    email       VARCHAR(255) PRIMARY KEY,
    code_hash   VARCHAR(64)  NOT NULL,
    attempts    SMALLINT     NOT NULL DEFAULT 0,
    expires_at  TIMESTAMPTZ  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
