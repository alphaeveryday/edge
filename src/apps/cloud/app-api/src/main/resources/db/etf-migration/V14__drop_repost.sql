-- 리포스트 기능 삭제
ALTER TABLE post DROP COLUMN repost_of_id, DROP COLUMN repost_count;
