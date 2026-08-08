-- 가설 원장 (ALPHA-881).
--
-- 인과 경로의 가설은 지금 stage_results 버퍼(stat_tests)에만 남고, **기각된 제안은
-- 어디에도 남지 않는다** — 무엇이 제안됐고 왜 죽었는지가 런 로그 보존 기간과 함께
-- 사라진다. 이 표가 그 원장이다: 제안 기각(REJECTED)과 검정 전건(TESTED)을 행으로
-- 남겨, "이 트리거에서 어떤 가설이 서고 죽었나"를 SQL 로 되짚는다.
--
-- trial_id 는 결정적 해시다: (trigger_id, stage, 정체성). 정체성은 TESTED 면 튜플
-- 슬롯(방아쇠·채널·노출·층·조건), REJECTED 면 기각 사유 원문.
--
-- 멱등 축 선택 — PK(trial_id) + writer 의 ON CONFLICT DO UPDATE 를 쓴다.
-- (trigger, run) UNIQUE 축을 안 쓴 이유: explanation_run_id 는 벽시계
-- (explanation_as_of)가 재료라 재배달·재실행마다 새 값이고, 그 축은 재실행마다
-- 행을 쌓는다. 결정적 trial_id 는 같은 트리거 재실행을 같은 행으로 수렴시키고,
-- DO UPDATE 가 판정·통계·run 연결을 최신 런으로 덮는다(정정 후 재실행이 낡은
-- 판정을 남기지 않는다).
--
-- minute_price_trigger_id 는 FK-성 TEXT 다 — 실제 FK 제약을 안 거는 이유:
-- minute_price_trigger 는 장중 매분 INSERT 를 받는 표라 참조 제약이 그 표의 락
-- 수명에 이 원장을 묶는다(락 예산, ALPHA-799). 계보 무결성은 writer 가
-- 소비한 trigger_id 에서만 행을 파생하는 것으로 지킨다.
CREATE TABLE IF NOT EXISTS hypothesis_trial (
    trial_id                TEXT             PRIMARY KEY,
    minute_price_trigger_id TEXT             NOT NULL,
    explanation_run_id      TEXT,
    trade_date              DATE             NOT NULL,
    ticker                  TEXT             NOT NULL,
    stage                   VARCHAR(16)      NOT NULL,
    trigger_slot            TEXT,
    channel                 TEXT,
    exposure                TEXT,
    layer                   TEXT,
    conditions              JSONB,
    verdict                 VARCHAR(20),
    applies_today           BOOLEAN,
    n                       INTEGER,
    p                       DOUBLE PRECISION,
    effect_low              DOUBLE PRECISION,
    effect_high             DOUBLE PRECISION,
    reason                  TEXT             NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ      NOT NULL DEFAULT now(),
    CONSTRAINT ck_hypothesis_trial_stage
        CHECK (stage IN ('PROPOSED', 'REJECTED', 'TESTED')),
    -- 저장은 영문 코드 원칙 — 코드의 한글 판정(성립·불성립·판정불가)은 writer 가
    -- ESTABLISHED / NOT_ESTABLISHED / UNDECIDABLE 로 옮겨 적는다. REJECTED 는
    -- 검정에 못 간 기각 제안의 판정이다.
    CONSTRAINT ck_hypothesis_trial_verdict
        CHECK (verdict IS NULL OR verdict IN
               ('ESTABLISHED', 'NOT_ESTABLISHED', 'UNDECIDABLE', 'REJECTED'))
);

CREATE INDEX IF NOT EXISTS ix_hypothesis_trial_trigger
    ON hypothesis_trial (minute_price_trigger_id);
CREATE INDEX IF NOT EXISTS ix_hypothesis_trial_cell
    ON hypothesis_trial (ticker, trade_date DESC);

COMMENT ON TABLE hypothesis_trial IS
    '가설 원장 — 트리거당 가설 제안 기각(REJECTED)·검정 전건(TESTED)의 행 단위 기록 (ALPHA-881)';
COMMENT ON COLUMN hypothesis_trial.trial_id IS
    '결정적 id: stable_id(trigger_id, stage, 튜플 정체성 또는 기각 사유) — 재실행 동일(멱등 축)';
COMMENT ON COLUMN hypothesis_trial.minute_price_trigger_id IS
    '이 가설 시행이 매달린 분봉 트리거 — FK-성 TEXT (락 예산 때문에 제약 없음)';
COMMENT ON COLUMN hypothesis_trial.explanation_run_id IS
    '이 시행을 영속한 설명 런 — 재실행 시 최신 런으로 갱신된다. NULL = 런 확정 전 실패';
COMMENT ON COLUMN hypothesis_trial.stage IS
    'PROPOSED(예약 - 현재 writer 미사용) · REJECTED(심사 기각 - 검정 미도달) · TESTED(패널 검정 완료)';
COMMENT ON COLUMN hypothesis_trial.trigger_slot IS
    '닫힌 어휘 원값 "종류:식별자" (예: 점:CONTRACT.SIGNING · 계열:거래량)';
COMMENT ON COLUMN hypothesis_trial.conditions IS
    '튜플의 조건 술어 원값 배열 (kind·ident·transform·comparator·percentile·lookback)';
COMMENT ON COLUMN hypothesis_trial.reason IS
    'REJECTED 는 심사 기각 사유 원문 · TESTED 는 판정불가/적용불가 사유 (성립·적용이면 빈 문자열)';
