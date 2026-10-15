-- ops_expected_task 에 산출 카운터 저장 (ALPHA-182).
--
-- `ops` 로그 봉투(ALPHA-181)의 `records_out`·`failed_records` 는 지금까지 `data_status` 파생에만
-- 쓰이고 **버려졌다** — 건수가 남는 곳은 S3 로그뿐이라, 운영 대시보드(ALPHA-514)가 "이 작업이 몇
-- 건을 냈나"를 그리려면 런×작업마다 S3 를 뒤져야 한다. 판정에 이미 쓰던 값을 그대로 싣는다.
--
-- **판정 로직은 바뀌지 않는다** — 저장 전용 컬럼이고 `data_status` 파생 규칙은 그대로다.
--
-- 봉투 결측·malformed(음수·NaN·비수치·소수) 신호는 **0 으로 메우지 않고 NULL** 로 남긴다.
-- `data_status` 가 근거 부족을 UNKNOWN 으로 남기는 것과 같은 결이다 — 0 으로 메우면 대시보드가
-- "0건 처리"와 "모름"을 못 가른다(Rule 12).
--
-- **행 하나의 카운터는 그 작업의 마지막 시도의 것이다**(판정이 아니라 시도 스코프). 쓰는 주체는
-- wrapper 하나이고, Reconciler 는 ECS 증거로 판정을 뒤집어도 건수를 모르므로 다시 쓰지 않는다.
--
-- 봉투 **스코프 규칙**(ALPHA-181)을 그대로 승계한다: 산출과 유실은 *이 런이 재판정한 범위*에서
-- 함께 온다. 처리분을 건너뛰는 스텝(tag-news·assemble-events·enrich-corp-code·
-- load-price-triggers)의 no-op 재실행은 0 건이며, 그 0 은 "이 런이 새로 낸 것이 없다"는 뜻이지
-- "데이터셋이 비었다"가 아니다.
--
-- CHECK(>= 0) 을 두지 않는 이유: 쓰기 경로(`wrapper._counter`)가 이미 유효 카운트만 통과시키는데,
-- CHECK 위반이 나면 같은 UPDATE 문에 실린 `task_outcome`·`data_status` 까지 통째로 롤백된다.
-- 그러면 실제로 끝난 작업이 PENDING 으로 남아 MISSED 로 오판된다 — 카운터 하나 지키자고 판정
-- 축을 잃는 거래는 성립하지 않는다.

SET search_path TO public;

ALTER TABLE ops_expected_task
    ADD COLUMN records_out    BIGINT,
    ADD COLUMN failed_records BIGINT;

COMMENT ON COLUMN ops_expected_task.records_out IS
'이 작업의 **마지막 시도**가 낸 유효 산출 건수(ops 봉투 records_out). 세는 범위는 그 시도가 재판정한 것 — 건너뛴 항목은 안 센다. 신호 결측·malformed 면 NULL(0 으로 메우지 않는다). Reconciler 는 판정을 뒤집어도 이 값을 다시 쓰지 않으므로, FAILED 옆의 건수는 앞 시도의 것일 수 있다.';

COMMENT ON COLUMN ops_expected_task.failed_records IS
'마지막 시도의 in-band 유실 건수(ops 봉투 failed_records). records_out 과 항상 같은 스코프에서 함께 쓰인다 — 비대칭이면 옛 실패가 산출로 뒤집힌다. 신호 결측·malformed 면 NULL.';
