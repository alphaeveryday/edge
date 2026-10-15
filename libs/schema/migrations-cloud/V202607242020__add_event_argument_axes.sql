-- ALPHA-544: v4 아규먼트 축 확장 (expand-only)
-- 참여자 다중역할 축(slot·mention·kind·group_ord)·값형 아규먼트(event_measure)·
-- 이벤트 grain 판별 메타(predicate·confidence_level·completeness)를 추가한다.
-- 전부 nullable 컬럼 추가 + 신규 테이블 — 기존 행·reader(분석엔진·tenant-sync) 무영향.
-- stage 통제어휘 CHECK 는 두지 않는다: 기존 lifecycle_stage 오염 행과 충돌하며,
-- 메뉴 강제는 추출 코드(ALPHA-545)가 담당한다.

-- ============================================================================
-- 1) source_event — 이벤트 grain 판별 메타 (v4 계약: type-predicate 판별·신뢰층·완결성)
-- ============================================================================

ALTER TABLE source_event
    ADD COLUMN predicate_code   VARCHAR(80),
    ADD COLUMN confidence_level VARCHAR(10),
    ADD COLUMN completeness     VARCHAR(10);

ALTER TABLE source_event
    ADD CONSTRAINT ck_source_event_confidence_level
        CHECK (confidence_level IS NULL OR confidence_level IN ('HIGH', 'MEDIUM', 'LOW')),
    ADD CONSTRAINT ck_source_event_completeness
        CHECK (completeness IS NULL OR completeness IN ('complete', 'partial'));

COMMENT ON COLUMN source_event.predicate_code IS
'v4 통제 술어 — 타입별 allowed_predicates 메뉴 내 값만 기록(코드측 강제, edge_ontology).';
COMMENT ON COLUMN source_event.confidence_level IS
'추출 신뢰층 HIGH/MEDIUM/LOW — v4 계약의 confidence 를 물리 enum 으로 사상.';
COMMENT ON COLUMN source_event.completeness IS
'필수 역할 충족 여부 complete/partial — required_roles 대비 조립 완결성.';

-- ============================================================================
-- 2) event_argument — 참여자 축 (v4 participants[])
-- ============================================================================

ALTER TABLE event_argument
    ADD COLUMN slot         VARCHAR(10),
    ADD COLUMN mention_text TEXT,
    ADD COLUMN entity_kind  VARCHAR(30),
    ADD COLUMN group_ord    SMALLINT;

ALTER TABLE event_argument
    ADD CONSTRAINT ck_event_argument_slot
        CHECK (slot IS NULL OR slot IN ('subject', 'object', 'qualifier'));

COMMENT ON COLUMN event_argument.slot IS
'인과 방향 슬롯 subject/object/qualifier — 역할쌍(원고/피고 등) 방향 질의의 기질.';
COMMENT ON COLUMN event_argument.mention_text IS
'원문 표면형(멘션) — 접지 감사·오접지 진단용.';
COMMENT ON COLUMN event_argument.entity_kind IS
'엔티티 종별(ISSUER·COMPANY_ENTITY·PRODUCT_OR_CONCEPT·COHORT·AUTHORITY_OR_RULE·LOCATION_OR_HAZARD·INDEX_OR_EXCHANGE) — 역할→종별 제약 검증 기질.';
COMMENT ON COLUMN event_argument.group_ord IS
'멀티기업 기사에서 (주체↔값) 짝 바인딩 서수 — event_measure.group_ord 와 대응.';

-- ============================================================================
-- 3) event_measure — 값형 아규먼트 (v4 measures[], 사건 grain 정량)
-- ============================================================================

CREATE TABLE event_measure (
    source_event_id     TEXT NOT NULL,
    measure_ord         SMALLINT NOT NULL,
    role_code           VARCHAR(80) NOT NULL,
    surface             TEXT,
    value               NUMERIC(30, 8),
    unit                VARCHAR(30),
    basis               VARCHAR(10) NOT NULL DEFAULT 'UNKNOWN',
    value_source        VARCHAR(12) NOT NULL DEFAULT 'UNRESOLVED',
    parse_flag          VARCHAR(30),
    group_ord           SMALLINT,
    dart_rcept_no       VARCHAR(20),

    PRIMARY KEY (source_event_id, measure_ord),

    CONSTRAINT ck_event_measure_basis
        CHECK (basis IN ('TOTAL', 'ANNUAL', 'UNKNOWN')),
    CONSTRAINT ck_event_measure_value_source
        CHECK (value_source IN ('PARSED', 'DART', 'UNRESOLVED')),
    CONSTRAINT fk_event_measure_source_event
        FOREIGN KEY (source_event_id)
        REFERENCES source_event (source_event_id)
        ON DELETE CASCADE
);

COMMENT ON TABLE event_measure IS
'사건 grain 값형 아규먼트(금액·비율·기간). 역할 메뉴는 타입별 quantities(edge_ontology), basis 는 TOTAL/ANNUAL 정규화, value_source 는 PARSED→DART 승격 레인(ALPHA-547)의 기질.';
COMMENT ON COLUMN event_measure.surface IS
'원문 표면형("1,883억원") — 파싱 감사와 골드 대조의 근거.';
COMMENT ON COLUMN event_measure.dart_rcept_no IS
'DART 접수번호 — value_source=DART 승격 시 공시 lineage.';

CREATE INDEX ix_event_measure_role ON event_measure (role_code);
