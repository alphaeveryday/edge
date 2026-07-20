-- ============================================================================
-- document_assertion.modality_code — NOT NULL 해제
--
-- 이 컬럼은 VARCHAR(30) NOT NULL 인데 **허용 어휘가 정의된 적이 없다.** CHECK 도, 문서도,
-- ADR 도 없고 상류 온톨로지(alphamale event_type_profiles v0.1)에도 modality 개념이 없다.
-- 값을 강제하면서 무엇이 합법적인 값인지는 아무도 모르는 상태라, 이 제약은 만족 불가능하다
-- — 태깅(ALPHA-138)이 이 값을 내지 않는 것도 같은 이유다.
--
-- 어휘를 여기서 발명하지 않는 이유: 임의 값(ASSERTED·UNKNOWN 류)을 적재하기 시작하면 그게
-- 사실상 계약이 되고, 나중에 진짜 어휘가 확정될 때 이미 적재된 행이 전부 오염된다. assertion 은
-- point-in-time 재현 대상이라 소급 수정이 특히 비싸다. 그래서 제약만 풀고 값은 비워 둔다.
--
-- 계약 확정 전까지 NULL 의 의미 = '문서가 어떤 양상으로 주장했는지 아직 기록하지 않음'.
-- 어휘가 확정되면 CHECK 추가 + NOT NULL 복원을 별도 마이그레이션으로 낸다 (그 시점엔 적재된
-- 행의 백필이 선행).
--
-- CHECK 를 지금 걸지 않는 건 V202607150001 의 명명 규약과도 일치한다
--   — "codes/status/version values: VARCHAR with CHECK constraints **where stable**".
--   modality 는 stable 하지 않다(미정의).
--
-- 확장-수축 단계: 순수 확장(제약 완화). NOT NULL 해제는 기존 writer 를 깨뜨리지 않으며,
--   애초에 이 테이블에는 reader/writer 코드가 존재한 적이 없다 (V202607150001·0002 와 동일 근거).
--
-- Refs: ALPHA-361 (선행 해제 대상 = ALPHA-190 적재)
-- ============================================================================

SET search_path TO public;

ALTER TABLE document_assertion
    ALTER COLUMN modality_code DROP NOT NULL;

COMMENT ON COLUMN document_assertion.modality_code IS
'문서가 사건을 주장한 양상. 허용 어휘 미확정(ALPHA-361) — 계약이 확정될 때까지 NULL 을 허용하며 임의 값을 생성하지 않는다. 어휘 확정 시 CHECK 추가 + NOT NULL 복원 예정.';
