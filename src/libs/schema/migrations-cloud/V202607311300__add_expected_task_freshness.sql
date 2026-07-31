-- Dataset Contract와 freshness evidence를 ops_expected_task에 추가 (ALPHA-654).
--
-- ADR-0043의 첫 수직 슬라이스는 ETF_HOLDINGS_COLLECTION_KRX다. KRX 응답에는 요청값과
-- 독립적인 actual-as-of evidence가 없으므로 writer는 actual_as_of_date=NULL,
-- freshness_status=UNKNOWN, freshness_reason=ACTUAL_AS_OF_UNVERIFIED를 기록한다.
-- adapter가 주입한 trd_dd, canonical as_of_date, fetched_at 또는 attempt 시각으로 실제
-- 기준일을 추정하지 않는다(ALPHA-653).
--
-- 모든 컬럼은 nullable이다. 기존 행은 계약 도입 전 사실을 그대로 보존하고, 구 reader도
-- 영향을 받지 않는다. 계약이 연결되지 않은 작업과 SKIPPED 작업의 freshness NULL은
-- NOT_APPLICABLE이며 UNKNOWN과 다르다.

SET search_path TO public;

ALTER TABLE ops_expected_task
    ADD COLUMN dataset_contract_key     TEXT,
    ADD COLUMN dataset_contract_version TEXT,
    ADD COLUMN dataset_contract_snapshot JSONB,
    ADD COLUMN actual_as_of_date         DATE,
    ADD COLUMN collected_at              TIMESTAMPTZ,
    ADD COLUMN observed_at               TIMESTAMPTZ,
    ADD COLUMN freshness_status          TEXT,
    ADD COLUMN freshness_reason          TEXT,
    ADD COLUMN freshness_evidence        JSONB,

    ADD CONSTRAINT ck_ops_expected_task_contract_snapshot CHECK (
        (dataset_contract_key IS NULL
            AND dataset_contract_version IS NULL
            AND dataset_contract_snapshot IS NULL)
        OR
        (dataset_contract_key IS NOT NULL
            AND dataset_contract_version IS NOT NULL
            AND dataset_contract_snapshot IS NOT NULL)
    ),
    ADD CONSTRAINT ck_ops_expected_task_freshness_status CHECK (
        freshness_status IS NULL
        OR freshness_status IN ('UNKNOWN', 'FRESH', 'STALE')
    ),
    ADD CONSTRAINT ck_ops_expected_task_freshness_pair CHECK (
        (freshness_status IS NULL AND freshness_reason IS NULL AND observed_at IS NULL)
        OR
        (freshness_status IS NOT NULL AND freshness_reason IS NOT NULL AND observed_at IS NOT NULL)
    ),
    ADD CONSTRAINT ck_ops_expected_task_freshness_applicability CHECK (
        dataset_contract_key IS NOT NULL
        OR (
            actual_as_of_date IS NULL
            AND freshness_status IS NULL
            AND freshness_reason IS NULL
            AND freshness_evidence IS NULL
        )
    ),
    ADD CONSTRAINT ck_ops_expected_task_verified_as_of CHECK (
        freshness_status NOT IN ('FRESH', 'STALE')
        OR actual_as_of_date IS NOT NULL
    );

COMMENT ON COLUMN ops_expected_task.dataset_contract_key IS
'Planner가 적용한 Dataset Contract의 안정적 key. NULL은 이 작업에 계약이 아직 연결되지 않았음을 뜻한다.';

COMMENT ON COLUMN ops_expected_task.dataset_contract_version IS
'Planner가 적용한 Dataset Contract의 opaque version. 현재 registry 값으로 과거 행을 소급 재판정하지 않는다.';

COMMENT ON COLUMN ops_expected_task.dataset_contract_snapshot IS
'계획 시점의 계약과 해석 결과 snapshot(JSONB). expected_as_of의 저장 SSOT는 별도 컬럼을 만들지 않고 기존 expected_as_of_date를 사용한다.';

COMMENT ON COLUMN ops_expected_task.actual_as_of_date IS
'실제 수신 데이터의 업무 기준일. 요청값과 독립된 source evidence로 검증될 때만 DATE로 저장하며, KRX trd_dd는 evidence가 아니다.';

COMMENT ON COLUMN ops_expected_task.collected_at IS
'task 범위의 immutable 수집 산출물과 로그가 저장되어 관측 가능해진 UTC 시각. 벤더 기준일이 아니다.';

COMMENT ON COLUMN ops_expected_task.observed_at IS
'계약과 evidence를 비교해 freshness를 평가한 UTC 시각. freshness가 적용되지 않은 행은 NULL이다.';

COMMENT ON COLUMN ops_expected_task.freshness_status IS
'데이터 기준일 상태의 독립 축: UNKNOWN·FRESH·STALE. NULL은 NOT_APPLICABLE이며 UNKNOWN과 다르다.';

COMMENT ON COLUMN ops_expected_task.freshness_reason IS
'freshness 판정 reason code. 예: ACTUAL_AS_OF_UNVERIFIED·AS_OF_MATCH·ACTUAL_AS_OF_BEFORE_EXPECTED.';

COMMENT ON COLUMN ops_expected_task.freshness_evidence IS
'freshness 판정에 사용한 source evidence 참조·hash 등 가변 상세(JSONB). 요청일·파티션일·attempt 시각으로 actual_as_of를 추정하지 않는다.';
