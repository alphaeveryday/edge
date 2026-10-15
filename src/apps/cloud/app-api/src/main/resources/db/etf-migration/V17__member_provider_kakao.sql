-- 카카오 소셜 로그인의 가입 방법 허용
ALTER TABLE member DROP CONSTRAINT ck_member_provider;
ALTER TABLE member ADD CONSTRAINT ck_member_provider CHECK (provider IN ('email', 'apple', 'google', 'kakao'));
