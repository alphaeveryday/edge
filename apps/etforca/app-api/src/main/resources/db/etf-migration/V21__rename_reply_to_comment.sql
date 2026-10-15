-- 답글 용어의 댓글 통일. 테이블·컬럼·색인·신고 대상 값의 comment 이름 변경.
ALTER TABLE reply RENAME TO comment;
ALTER TABLE comment RENAME CONSTRAINT reply_pkey TO comment_pkey;
ALTER SEQUENCE reply_id_seq RENAME TO comment_id_seq;
ALTER INDEX ix_reply_post RENAME TO ix_comment_post;

ALTER TABLE post RENAME COLUMN reply_count TO comment_count;

ALTER TABLE notification RENAME COLUMN reply_id TO comment_id;
ALTER INDEX ix_notification_reply RENAME TO ix_notification_comment;

ALTER TABLE report DROP CONSTRAINT ck_report_target_type;
UPDATE report SET target_type = 'comment' WHERE target_type = 'reply';
ALTER TABLE report ADD CONSTRAINT ck_report_target_type CHECK (target_type IN ('post', 'comment'));
