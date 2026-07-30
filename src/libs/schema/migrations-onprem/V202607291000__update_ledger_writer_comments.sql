-- ============================================================================
-- explanations 쓰기 실전환(ALPHA-613) 반영 — writer 분담 COMMENT 정정
--
-- explanations 사후 운영 쓰기(제공 중단·검수 이관·최종 문구 정정)가 mock 에서 원장
-- 전이로 전환되며 tenant-console-api 의 writer 표면이 넓어졌다. 적용된 마이그레이션을
-- 수정하지 않고 새 마이그레이션으로 COMMENT 만 갱신한다(comment-only, additive —
-- 선례 V202607211900). 구조 변경 없음.
-- ============================================================================

SET search_path TO public;

-- analysis_item: 검수 결정 전이(ReviewService)에 더해 사후 운영 전이가 추가된다 —
-- 수동 제공 중단(AUTO_PUBLISHED|APPROVED → UNPUBLISHED)·검수 이관(BLOCKED →
-- REVIEW_REQUIRED). 전이별 분담 규율은 유지된다(같은 전이를 두 모듈이 쓰지 않는다).
COMMENT ON TABLE analysis_item IS
'분석 항목 상태 원장(state-machine.md) — PK = Cloud 발번 explanation_result_id(멱등 upsert 키). writer 는 전이별 분담: screening-worker(수신·자동 분기·Cloud 이벤트 반영), tenant-console-api(검수 결정 전이 + 사후 운영 전이: 수동 제공 중단·검수 이관).';

-- publication: 검수 승인 재발행에 더해 사후 운영 쓰기가 추가된다 — 최종 문구 정정
-- (published_summary in-place UPDATE)·수동 제공 중단(PUBLISHED → UNPUBLISHED + 사유·
-- 실행자·시각 메타). 게시 grain 규율(부분 유니크)은 불변.
COMMENT ON TABLE publication IS
'게시 원장(Published Store) — publication-api 서빙 소스. writer 는 전이별 분담: screening-worker(자동 게시·무효화·정정 UNPUBLISHED), tenant-console-api(검수 승인 재발행 + 사후 운영: 최종 문구 정정·수동 제공 중단).';

-- analysis_item_status_history: 사후 운영 전이(수동 중단·검수 이관)도 tenant-console-api
-- 가 같은 트랜잭션에서 MEMBER 이력으로 기록한다 — analysis_item COMMENT 의 writer
-- 분담과 동일하게 status_history 소유 서술도 넓혀 SSOT 정합을 유지한다.
COMMENT ON TABLE analysis_item_status_history IS
'analysis_item 상태 변경 이력(append-only) — writer 는 analysis_item 과 동일한 전이 소유 분리를 따른다(각 모듈은 자기가 만든 전이만 같은 트랜잭션에서 기록): screening-worker = 자동 분기·Cloud 이벤트 반영, tenant-console-api = 검수 결정 + 사후 운영 전이(수동 제공 중단·검수 이관).';
