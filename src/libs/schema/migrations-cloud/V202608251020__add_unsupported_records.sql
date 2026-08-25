-- 마지막 시도의 정상 지원 제외 건수를 실행 원장에 보존한다 (ALPHA-1019).
--
-- `failed_records`는 실제 유실이라 data_status 판정에 참여한다. 현금·옵션처럼 입력에는 있지만
-- 현재 적재 대상이 아닌 행을 거기에 더하면 정상 런이 INCOMPLETE가 된다. 별도 저장 축을 두되,
-- 판정 규칙은 바꾸지 않는다.
--
-- DEFAULT 없는 nullable 컬럼이다. 기존 행과 아직 계측하지 않는 작업의 NULL은 "모름"이고,
-- 새 producer가 기록하는 0만 "지원 제외 없음"이다. 과거 행을 백필하거나 다시 스캔하지 않는다.
-- 이 expand 마이그레이션을 dev에 먼저 적용한 뒤 ALPHA-1020이 writer/API/UI를 배선한다. 앱 배포는
-- schema-migrate 완료를 기다리지 않으므로 같은 PR에 묶으면 코드가 컬럼보다 먼저 배포될 수 있다.
-- CHECK를 두지 않는 이유는 기존 records_out/failed_records와 같다. writer가 유효한 비음수
-- 정수만 통과시키며, 카운터 하나의 제약 위반으로 같은 UPDATE의 작업 귀결까지 잃지 않는다.

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_expected_task
    ADD COLUMN unsupported_records BIGINT;

COMMENT ON COLUMN ops_expected_task.unsupported_records IS
'이 작업의 마지막 시도가 정상적으로 지원 제외한 입력 건수. failed_records와 달리 유실·INCOMPLETE 판정에 참여하지 않는다. NULL은 과거 또는 계측 없음이고 0은 지원 제외 없음이다. 재스캔·백필하지 않는다.';
