-- ============================================================================
-- policy_version 자동 제공 위험등급 상한 — max_risk (ALPHA-438)
--
-- V202607221020 이 "산정 주체(Cloud vs Screening Worker) 미결정(TODO.md §2)"을
-- 사유로 보류했던 컬럼. 산정 주체가 온프렘 Screening Worker 로 확정(2026-07-26,
-- ALPHA-429 코멘트)되어 보류가 해소됐다 — 콘솔 "자동 제공 기준" 화면(허용 위험
-- 등급 LOW/MEDIUM)의 저장 원천으로 확장한다.
--
-- NULL 허용 = 미설정(기존 행·조건 없음). HIGH 는 어휘에 없다 — 고위험은 항상
-- 검수·차단 경로라 상한이 될 수 없다(콘솔 UI 계약 MAX_RISKS 와 동일).
-- 평가기(screening-worker) 소비는 위험등급 산정 구현 티켓에서 — 이 확장은
-- 저장·표시(콘솔 발행 모델)까지다.
-- ============================================================================

SET search_path TO public;

ALTER TABLE policy_version
    ADD COLUMN max_risk VARCHAR(10);

ALTER TABLE policy_version
    ADD CONSTRAINT ck_policy_version_max_risk
        CHECK (max_risk IS NULL OR max_risk IN ('LOW', 'MEDIUM'));

COMMENT ON COLUMN policy_version.max_risk IS
'자동 제공 위험등급 상한(LOW/MEDIUM, NULL=미설정) — 콘솔 "자동 제공 기준" 저장 원천(ALPHA-438). 평가기 소비는 위험등급 산정 구현에서(산정 주체=온프렘 Screening Worker, 2026-07-26 확정).';
