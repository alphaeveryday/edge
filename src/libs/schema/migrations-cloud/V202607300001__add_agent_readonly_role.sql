-- 에이전트 읽기전용 DB 롤 — 비밀번호가 없는 접속 주체.
--
-- 왜 필요한가. 지금 분석 태스크는 RDS 마스터 유저로 붙는다. 설명 한 줄을 쓰려고 DROP
-- 권한을 들고 있는 셈이다. 게다가 에이전트가 원장을 확인할 경로가 아예 없어서, 인과
-- 하네스의 SQL 이 실제 Postgres 에서 한 번도 실행되지 않은 채 머지됐다(ALPHA-620).
-- 가짜 커서는 SQL 을 파싱하지 않는다 — 검증했다고 말할 수 없는 상태였다.
--
-- 그래서 읽기만 가능한 별도 주체를 만든다. 읽기전용을 애플리케이션 정규식으로 주장하지
-- 않는다. 정규식은 우회가 남고, 우회 하나가 원장을 지운다. **권한이 없으면 문법이
-- 통과해도 서버가 거부한다** — 그게 이 롤의 존재 이유다.
--
-- 비밀번호를 부여하지 않는다. RDS IAM 인증(rds_iam)으로 붙으므로 15분 만료 토큰이
-- 매번 새로 발급되고, 유출될 장기 비밀이 존재하지 않는다. 회수는 DB 가 아니라 IAM 에서
-- 한다(rds-db:connect 를 떼면 즉시 끊긴다). 그래서 비밀번호 회전 절차도 필요 없다.
--
-- rds_iam 은 RDS 관리 인스턴스에만 있는 롤이다. 로컬·e2e Postgres 에는 없으므로 존재할
-- 때만 부여한다. 이 조건부가 없으면 같은 마이그레이션이 로컬에서 깨지고, 로컬에서
-- 안 도는 마이그레이션은 결국 클라우드에서만 검증되는 코드가 된다.
--
-- LOGIN 은 주되 NOINHERIT·NOCREATEDB·NOCREATEROLE 은 기본값이라 명시하지 않는다.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
    CREATE ROLE agent_ro LOGIN;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rds_iam') THEN
    GRANT rds_iam TO agent_ro;
  END IF;
END
$$;

-- 스키마 진입과 현재 테이블 전부에 SELECT.
GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_ro;

-- 앞으로 생기는 테이블에도 자동 적용. 이게 없으면 새 마이그레이션마다 GRANT 를 잊고,
-- 잊은 채로 에이전트가 "테이블이 없다"고 잘못 결론 내린다(권한 오류를 부재로 오독).
--
-- DEFAULT PRIVILEGES 는 **부여자별로** 걸린다. Flyway 가 마스터 유저로 DDL 을 돌리므로
-- FOR ROLE 을 CURRENT_USER 로 고정해야 실제 생성자와 일치한다.
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
  GRANT SELECT ON TABLES TO agent_ro;

-- 시퀀스·함수 권한은 주지 않는다. nextval 은 쓰기이고, 함수는 SECURITY DEFINER 로
-- 권한을 우회할 수 있다. 읽기에 필요하지 않은 것은 주지 않는다.
