-- rds_iam 멤버십 회수 — 마스터 인증을 죽인 상속 지뢰 제거 (ALPHA-933).
--
-- V202607300001 이 agent_ro 에 GRANT rds_iam 을 걸었고(IAM 토큰 접속 설계),
-- V202608111500 이 GRANT agent_ro TO 마스터를 걸자 마스터가 rds_iam 을 **상속**해
-- RDS 가 마스터의 비밀번호 인증을 PAM(IAM)으로 돌렸다 — 조직 SCP 가 rds-db:connect
-- 를 막는 계정이라 전 앱의 신규 접속이 전면 실패했다(2026-08-11 dev 실증, 수동
-- IAM 인증 비활성으로 복구). IAM 토큰 경로는 SCP 로 원리적으로 불가하므로 이
-- 멤버십은 죽은 설계의 잔재이자 지뢰다 — 회수한다.
--
-- 로컬·e2e 에는 rds_iam 이 없어 조건부다(V202607300001 의 부여도 같은 조건이었다).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rds_iam') THEN
    REVOKE rds_iam FROM agent_ro;
  END IF;
END
$$;
