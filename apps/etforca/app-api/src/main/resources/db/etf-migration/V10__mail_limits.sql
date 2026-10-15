-- 코드 메일 이메일당 하루 발송 상한용 발송 수와 24시간 창 시작 시각.
ALTER TABLE password_reset_code
    ADD COLUMN sent_count        SMALLINT    NOT NULL DEFAULT 0,
    ADD COLUMN window_started_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE signup_code
    ADD COLUMN sent_count        SMALLINT    NOT NULL DEFAULT 0,
    ADD COLUMN window_started_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- 전체 하루 메일 발송 수. 날짜는 KST.
CREATE TABLE mail_daily (
    day   DATE    PRIMARY KEY,
    sent  INTEGER NOT NULL
);
