-- 같은 푸시 토큰은 기기 하나에만, 회원 단위 발송 조회용 색인
CREATE UNIQUE INDEX uq_device_push_token ON device (push_token) WHERE push_token IS NOT NULL;
CREATE INDEX ix_device_member_push ON device (member_id) WHERE push_token IS NOT NULL;
