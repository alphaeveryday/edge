-- 알림 종류를 디자인이 쓰는 관심·커뮤니티 둘로 축소.
ALTER TABLE notification DROP CONSTRAINT ck_notification_kind;
ALTER TABLE notification ADD CONSTRAINT ck_notification_kind CHECK (kind IN ('watch', 'comm'));
