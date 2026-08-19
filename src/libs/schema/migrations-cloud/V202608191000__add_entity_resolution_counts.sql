-- 뉴스 assertion 엔티티 해소율의 원장 저장 표면 (ALPHA-999).
--
-- `load_assertions` 는 이미 entity 역할 argument 의 해소 분자·분모를 quality log 에 남긴다.
-- 후속 writer 가 런별 추이를 RDS 에 보존할 수 있도록 논리 작업 행에 두 카운터를 추가한다.
-- 둘은 argument 단위이며, 비실체 역할은 분모 밖이다(`denominator=entity_roles_only`).
--
-- nullable + 기본값 없음: 기존 행은 측정하지 않은 과거다. 0 으로 채우면 "해소 대상 0건"과
-- "계측 없음"이 합쳐지므로 백필하지 않는다. 미해소 사유 분류는 이 표면의 소관이 아니다.
--
-- `ops_expected_task` 는 실행 중에도 갱신되는 원장이다. DEFAULT 없는 nullable 컬럼 추가라 보유
-- 시간은 상수지만 ACCESS EXCLUSIVE 획득이 장시간 대기하면 뒤의 wrapper UPDATE 가 줄을 선다.

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_expected_task
    ADD COLUMN entity_resolution_arguments_total    BIGINT,
    ADD COLUMN entity_resolution_arguments_resolved BIGINT,
    ADD CONSTRAINT ck_ops_expected_task_entity_resolution_counts CHECK (
        (entity_resolution_arguments_total IS NULL
            AND entity_resolution_arguments_resolved IS NULL)
        OR (
            entity_resolution_arguments_total IS NOT NULL
            AND entity_resolution_arguments_resolved IS NOT NULL
            AND entity_resolution_arguments_total >= 0
            AND entity_resolution_arguments_resolved >= 0
            AND entity_resolution_arguments_resolved <= entity_resolution_arguments_total
        )
    ) NOT VALID;

COMMENT ON COLUMN ops_expected_task.entity_resolution_arguments_total IS
'마지막 시도의 뉴스 assertion 엔티티 해소 분모(argument 수). entity 역할만 세고 비실체 역할은 제외한다. NULL은 계측 없음이며 기존 행은 백필하지 않는다.';

COMMENT ON COLUMN ops_expected_task.entity_resolution_arguments_resolved IS
'마지막 시도의 뉴스 assertion 엔티티 해소 분자(argument 수). quality log argument_resolution.resolved_any(티커·명부·채번으로 실제 접지된 전체)를 저장한다. entity_resolution_arguments_total과 같은 시도·범위에서 함께 기록하며 NULL은 계측 없음이다.';
