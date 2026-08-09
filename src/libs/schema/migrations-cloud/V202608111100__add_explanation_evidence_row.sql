-- 근거 데이터 구조 v3 — 근거 조각 1행 (ALPHA-888, 스펙 .tmp/근거 포맷에 대한 정리.md).
--
-- 저장 단위는 "근거 조각 1개 = 1행"이고 완성 문장을 저장하지 않는다(§0). 유형은
-- 6종(가격·구성종목·공시·뉴스·재무및컨센서스·통계검정)이며 저장은 전부 영문 코드다 —
-- 한글 라벨은 뷰 계층(콘솔 labels.ts, 엔진 참조 구현 evidence_render.py)이 만든다.
--
-- 플랫 5종은 content(내용)·source(출처)·observed_at(시각) 3줄 골격만 갖고,
-- 통계검정(STAT_TEST)은 content 대신 template+slots 를 저장한다(§3.1) — 렌더가
-- 조회 시 문형에 슬롯을 채워 조립하므로 완성 문장이 DB 에 남지 않는다. 시각 줄도
-- 없다(§3.4) — 검정 계산 시각은 감사 전용 test_as_of 로만 남는다.
--
-- 멱등 축: evidence_row_id = stable_id('evr', explanation_result_id, ref).
-- 같은 결과에 대한 재적재(재배달)는 같은 id 로 수렴해 upsert 된다. 결과 축이
-- explanation_result_id 인 이유: 근거 행은 특정 설명 산출물의 문장들에 매달린
-- 조각이라, 런이 다시 돌면 새 결과와 함께 새 행 세트가 서는 것이 맞다(가설 원장
-- hypothesis_trial 이 트리거 축인 것과 대조 — 그쪽은 전 수명주기, 여기는 통과분만).
--
-- explanation_result_id 는 FK-성 TEXT 다 — hypothesis_trial 과 같은 이유로 실제
-- FK 제약을 걸지 않는다(참조 제약이 결과 표의 락 수명에 이 표를 묶는다). 계보
-- 무결성은 writer 가 persist_explanation 이 돌려준 result_id 에서만 행을 파생하는
-- 것으로 지킨다.
--
-- effect_low / effect_high 는 지금 항상 NULL 이다 — 칼만 배선(ALPHA-803,
-- feature/ALPHA-803-kalman-wiring)이 머지되면 검정 effect 신뢰폭이 채워진다.
-- 그 전까지 소비자는 NULL 을 "신뢰폭 미배선"으로 읽는다.
CREATE TABLE IF NOT EXISTS explanation_evidence_row (
    evidence_row_id       TEXT             PRIMARY KEY,
    explanation_result_id TEXT             NOT NULL,
    explanation_run_id    TEXT,
    ref                   INTEGER          NOT NULL,
    evidence_type         VARCHAR(16)      NOT NULL,
    -- 3줄 골격(§1). STAT_TEST 는 content 가 없고(문형은 template+slots 로 조립)
    -- observed_at 도 없다(§3.4 시각 줄 생략).
    content               TEXT,
    source                TEXT             NOT NULL,
    observed_at           TEXT,
    -- 통계검정 추가정보(§3.1) — 플랫 5종은 전부 NULL.
    template              VARCHAR(16),
    basis                 VARCHAR(8),
    method                VARCHAR(20),
    slots                 JSONB,
    n                     INTEGER,
    unit                  VARCHAR(8),
    estimate              DOUBLE PRECISION,
    p                     DOUBLE PRECISION,
    k                     INTEGER,
    band                  VARCHAR(12),
    series                JSONB,
    effect_low            DOUBLE PRECISION,
    effect_high           DOUBLE PRECISION,
    -- 감사 전용(§3.1) — 카드에 렌더하지 않는다.
    test_as_of            TIMESTAMPTZ,
    null_kind             VARCHAR(8),
    created_at            TIMESTAMPTZ      NOT NULL DEFAULT now(),
    -- ref 는 정수이고 카드(결과)당 유일하다(§1 dedup: 동일 ref 는 카드당 1행).
    CONSTRAINT uq_explanation_evidence_row_ref
        UNIQUE (explanation_result_id, ref),
    CONSTRAINT ck_evidence_row_type CHECK (evidence_type IN
        ('PRICE', 'HOLDING', 'DISCLOSURE', 'NEWS', 'FINANCIAL', 'STAT_TEST')),
    -- 유형별 골격 강제(§1): 플랫은 content 필수·검정 필드 금지, 검정은 그 반대.
    CONSTRAINT ck_evidence_row_shape CHECK (
        (evidence_type <> 'STAT_TEST'
         AND content IS NOT NULL
         AND template IS NULL AND basis IS NULL AND method IS NULL
         AND slots IS NULL AND n IS NULL AND unit IS NULL
         AND estimate IS NULL AND p IS NULL AND k IS NULL AND band IS NULL
         AND series IS NULL AND null_kind IS NULL)
        OR
        (evidence_type = 'STAT_TEST'
         AND content IS NULL AND observed_at IS NULL
         AND template IS NOT NULL AND basis IS NOT NULL AND method IS NOT NULL
         AND slots IS NOT NULL AND n IS NOT NULL AND unit IS NOT NULL
         AND estimate IS NOT NULL AND p IS NOT NULL AND k IS NOT NULL
         AND series IS NOT NULL AND null_kind IS NOT NULL)),
    CONSTRAINT ck_evidence_row_template CHECK (template IS NULL OR template IN
        ('MATCHED_ATT', 'MARKET_EVENT', 'TUPLE_PANEL', 'RELATION_PANEL',
         'MODERATION', 'EVENT_TAIL')),
    CONSTRAINT ck_evidence_row_basis CHECK (basis IS NULL OR basis IN
        ('MARKET', 'SECTOR', 'IDIO')),
    CONSTRAINT ck_evidence_row_method CHECK (method IS NULL OR method IN
        ('SIMILAR_STOCKS', 'SIMILAR_DAYS', 'SENSITIVE_STOCKS', 'RELATED_STOCKS',
         'BY_CONDITION', 'VS_USUAL')),
    CONSTRAINT ck_evidence_row_unit CHECK (unit IS NULL OR unit IN ('COUNT', 'DAY')),
    CONSTRAINT ck_evidence_row_band CHECK (band IS NULL OR band IN
        ('TOP_TAIL', 'UPPER', 'MIDDLE', 'LOWER', 'BOTTOM_TAIL')),
    -- date 귀무는 순환이라 게이트에서 이미 걸러진다(§5) — 표도 거부한다.
    CONSTRAINT ck_evidence_row_null_kind CHECK (null_kind IS NULL OR null_kind IN
        ('label', 'pair')),
    CONSTRAINT ck_evidence_row_ref_nonneg CHECK (ref >= 0)
);

CREATE INDEX IF NOT EXISTS ix_evidence_row_result
    ON explanation_evidence_row (explanation_result_id, ref);
CREATE INDEX IF NOT EXISTS ix_evidence_row_run
    ON explanation_evidence_row (explanation_run_id);

COMMENT ON TABLE explanation_evidence_row IS
    '근거 데이터 v3 — 설명 결과당 근거 조각 1행. 플랫 5종 + 통과한 통계검정만 (ALPHA-888)';
COMMENT ON COLUMN explanation_evidence_row.evidence_row_id IS
    '결정적 id: stable_id(evr, explanation_result_id, ref) — 재적재 upsert 의 멱등 축';
COMMENT ON COLUMN explanation_evidence_row.explanation_result_id IS
    '이 근거가 매달린 설명 결과 — FK-성 TEXT (락 예산 때문에 제약 없음)';
COMMENT ON COLUMN explanation_evidence_row.ref IS
    '카드 내 근거 번호(정수, §1) — 유형 순서 고정 후 유형 내 오름차순으로 채번';
COMMENT ON COLUMN explanation_evidence_row.content IS
    '플랫 5종의 내용 줄(§2). STAT_TEST 는 NULL — 문형은 template+slots 가 조립한다';
COMMENT ON COLUMN explanation_evidence_row.observed_at IS
    '플랫 5종의 시각 줄(MM-DD HH:mm, §4). STAT_TEST 는 NULL — 시각 줄 자체가 없다(§3.4)';
COMMENT ON COLUMN explanation_evidence_row.slots IS
    '문형 슬롯(§3.5) — id·코드 저장, 렌더 시 이름 해소';
COMMENT ON COLUMN explanation_evidence_row.estimate IS
    '평균 차이 — 비율로 저장(§3.1), 렌더가 ×100 해 %p 로 만든다';
COMMENT ON COLUMN explanation_evidence_row.effect_low IS
    '검정 effect 신뢰폭 하한 — 칼만 배선(ALPHA-803) 머지 전까지 NULL';
COMMENT ON COLUMN explanation_evidence_row.effect_high IS
    '검정 effect 신뢰폭 상한 — 칼만 배선(ALPHA-803) 머지 전까지 NULL';
COMMENT ON COLUMN explanation_evidence_row.test_as_of IS
    '감사 전용 — 검정 계산 시각. 카드에 렌더하지 않는다(§3.1)';
COMMENT ON COLUMN explanation_evidence_row.null_kind IS
    '감사 전용 — label|pair. date 는 게이트(§5)가 걸러 표에 오지 못한다';
