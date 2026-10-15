-- 탈퇴 시 애플 로그인 연결 철회용 refresh token, 회원당 1행
CREATE TABLE apple_token (
    member_id      BIGINT         PRIMARY KEY REFERENCES member(id),
    refresh_token  VARCHAR(1024)  NOT NULL,
    updated_at     TIMESTAMPTZ    NOT NULL DEFAULT now()
);
