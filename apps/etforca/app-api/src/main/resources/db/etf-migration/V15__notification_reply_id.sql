-- 답글 삭제 시 그 답글로 생긴 알림의 함께 삭제. 기존 알림은 NULL 로 남는다.
ALTER TABLE notification ADD COLUMN reply_id BIGINT;
CREATE INDEX ix_notification_reply ON notification (reply_id) WHERE reply_id IS NOT NULL;
