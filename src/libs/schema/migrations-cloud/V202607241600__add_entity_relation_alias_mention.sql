-- =============================================================================
-- 엔티티 관계·별칭·멘션 — 온톨로지 엔티티 체계 1단계 (ALPHA-509)
-- =============================================================================
-- 계약 문서: docs/contracts/entity-relations.md (관계 어휘·방향·병합 규칙 SSOT)
-- 결정 기록: docs/adr/0039-entity-relation-schema.md
--
-- 무엇을 푸나:
--   1) entity_relation — 온톨로지 thread 계약이 선언만 하고(owns·supplies·member_of 등
--      13종 중 관계형 9종) 저장처가 없던 엔티티-엔티티 관계를 단방향+유효기간으로 적재.
--   2) entity_alias — 해소기(entity_resolution)가 완전일치 3축뿐이라 쌓이기만 하던
--      top_unresolved 를 수확할 별칭 축. 매칭 키는 정규화 문자열(alias_norm).
--   3) entity_mention — "미해소 = 스킵 + quality log 계측" 원칙의 사각지대 보완.
--      스킵된 표면 문자열이 feature 존 parquet 에만 남아 DB 재해소·중복 집계가
--      불가능했다. 원문 보존(supply_contract_fact.counterparty_raw_name 전례)을
--      일반화하고, UNKNOWN thread 재평가 승격 패턴(event_thread_link)과 동형으로
--      재해소 배치의 입력이 된다.
--
-- 어휘 정책 (V202607150003 · ADR-0027 전례 승계):
--   * relation_code / kind_hint 는 온톨로지 소관 어휘라 CHECK 로 발명하지 않는다.
--     허용값은 docs/contracts/entity-relations.md 가 정의하고 적재 코드가 검증한다.
--   * resolution_status / source_kind / alias_type 은 이 마이그레이션과 함께
--     확정하는 edge 소유 구조 어휘라 CHECK 로 못박는다.
--   * PK 는 파생/링크 테이블 전례(assertion_argument)를 따라 BIGINT IDENTITY.
--     도메인 ID(ADR-0027 ULID)는 마스터 객체 전용이다.
-- =============================================================================

-- ── 1. entity_relation — 엔티티 간 단방향 관계 (참조 지식 + 이벤트 파생 2층) ──

CREATE TABLE entity_relation (
    entity_relation_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_entity_id   TEXT NOT NULL,
    -- 어휘는 계약 문서가 정의한다: 참조 층 5종(ceo_of·officer_of·subsidiary_of·
    -- owns_brand·produces) + 이벤트 파생 9종. CHECK 없음 — 위 어휘 정책.
    relation_code       TEXT NOT NULL,
    object_entity_id    TEXT NOT NULL,
    -- 유효기간은 이벤트 파생 층 전용(개시 = EFFECTIVE 도달, 마감 = terminal/역이벤트).
    -- REFERENCE 층은 "현재 지식만" — 기간 없이 NULL, 사실 변경 시 행 교체(업서트).
    valid_from          DATE,
    valid_to            DATE,
    -- 병합 우선순위: DECLARED(공시 fact) > EVENT_DERIVED(뉴스 thread) > REFERENCE(큐레이션·마스터 피드).
    source_kind         VARCHAR(20) NOT NULL,
    source_thread_id    TEXT,
    source_fact_id      TEXT,
    confidence          DOUBLE PRECISION,
    asof                TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_entity_relation_not_self
        CHECK (subject_entity_id <> object_entity_id),
    CONSTRAINT ck_entity_relation_code
        CHECK (NULLIF(BTRIM(relation_code), '') IS NOT NULL),
    CONSTRAINT ck_entity_relation_source_kind
        CHECK (source_kind IN ('DECLARED', 'EVENT_DERIVED', 'REFERENCE')),
    -- 소스별 근거 참조를 강제한다: EVENT_DERIVED 는 thread, DECLARED 는 fact.
    -- REFERENCE 는 문서 단건이 아니라 현재-상태 지식이라 근거 참조가 없다(멱등키는 s·r·o).
    CONSTRAINT ck_entity_relation_source_ref
        CHECK (
            (source_kind = 'EVENT_DERIVED' AND source_thread_id IS NOT NULL AND source_fact_id IS NULL)
            OR
            (source_kind = 'DECLARED' AND source_fact_id IS NOT NULL AND source_thread_id IS NULL)
            OR
            (source_kind = 'REFERENCE' AND source_thread_id IS NULL AND source_fact_id IS NULL)
        ),
    CONSTRAINT ck_entity_relation_dates
        CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT ck_entity_relation_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

COMMENT ON TABLE entity_relation IS
'엔티티 간 단방향 관계(subject→object). REFERENCE 층은 현재-상태 지식(업서트·무이력), EVENT_DERIVED/DECLARED 층은 유효기간을 갖는 이벤트 파생. 어휘·방향·병합 규칙은 docs/contracts/entity-relations.md 가 정의한다.';

-- 재실행 멱등키: 한 thread(또는 공시 fact)는 관계 코드당 관계 행 하나만 만들고,
-- 참조 층은 (subject, relation, object) 현재 상태 한 행만 유지한다(업서트 키).
CREATE UNIQUE INDEX uq_entity_relation_thread
    ON entity_relation (source_thread_id, relation_code)
    WHERE source_thread_id IS NOT NULL;
CREATE UNIQUE INDEX uq_entity_relation_fact
    ON entity_relation (source_fact_id, relation_code)
    WHERE source_fact_id IS NOT NULL;
CREATE UNIQUE INDEX uq_entity_relation_reference
    ON entity_relation (subject_entity_id, relation_code, object_entity_id)
    WHERE source_kind = 'REFERENCE';

CREATE INDEX ix_entity_relation_subject ON entity_relation (subject_entity_id, relation_code);
CREATE INDEX ix_entity_relation_object ON entity_relation (object_entity_id, relation_code);

-- ── 2. entity_alias — 캐노니컬 엔티티의 별칭 (해소 4축째) ────────────────────

CREATE TABLE entity_alias (
    entity_alias_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id           TEXT NOT NULL,
    alias_text          TEXT NOT NULL,
    -- 매칭 키. 정규화 규칙(공백·법인 접미사 제거 등)은 계약 문서가 정의한다.
    alias_norm          TEXT NOT NULL,
    alias_type          VARCHAR(30) NOT NULL,
    source_code         VARCHAR(50),
    valid_from          DATE,
    valid_to            DATE,
    -- 동명 충돌 마커. 해소기는 TRUE 별칭을 결정적 매칭에서 제외한다
    -- (normalize_news 의 mention_index_ambiguous_names 제외 전례).
    is_ambiguous        BOOLEAN NOT NULL DEFAULT FALSE,

    CONSTRAINT uq_entity_alias UNIQUE (entity_id, alias_norm),
    CONSTRAINT ck_entity_alias_type
        CHECK (alias_type IN ('FULL_NAME', 'ABBREV', 'TICKER', 'ENGLISH_NAME', 'OLD_NAME', 'CURATED')),
    CONSTRAINT ck_entity_alias_norm
        CHECK (NULLIF(BTRIM(alias_norm), '') IS NOT NULL),
    CONSTRAINT ck_entity_alias_dates
        CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_to >= valid_from)
);

COMMENT ON TABLE entity_alias IS
'캐노니컬 엔티티의 별칭 사전. entity_resolution 완전일치 3축에 이은 4축째 매칭 입력이며, 같은 alias_norm 이 여러 엔티티에 붙으면 동명 충돌로 미해소 처리한다.';

CREATE INDEX ix_entity_alias_norm ON entity_alias (alias_norm);

-- ── 3. entity_mention — 표면 문자열 관측과 해소 상태 ─────────────────────────

CREATE TABLE entity_mention (
    entity_mention_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id         TEXT NOT NULL,
    -- NULL = 문서 수준 멘션(제목/리드 탐지). NOT NULL = 주장 argument 수준.
    assertion_id        TEXT,
    role_code           VARCHAR(80),
    surface_text        TEXT NOT NULL,
    normalized_text     TEXT NOT NULL,
    -- 온톨로지 entity_kind 힌트(ISSUER·COMPANY_ENTITY 등 7종). CHECK 없음 — 어휘 정책.
    kind_hint           VARCHAR(40),
    resolution_status   VARCHAR(20) NOT NULL,
    entity_id           TEXT,
    resolver_version    VARCHAR(50) NOT NULL,
    confidence          DOUBLE PRECISION,
    available_at        TIMESTAMPTZ NOT NULL,
    resolved_at         TIMESTAMPTZ,

    CONSTRAINT ck_entity_mention_status
        CHECK (resolution_status IN ('RESOLVED', 'UNRESOLVED', 'AMBIGUOUS')),
    -- event_thread_link 의 UNKNOWN↔NULL 강제와 동형: 해소됐으면 반드시 링크가 있고,
    -- 안 됐으면 반드시 없다 — placeholder 엔티티 행 생성을 스키마가 차단한다.
    CONSTRAINT ck_entity_mention_resolved
        CHECK (
            (resolution_status = 'RESOLVED' AND entity_id IS NOT NULL)
            OR
            (resolution_status <> 'RESOLVED' AND entity_id IS NULL)
        ),
    -- 문서 수준 멘션은 역할이 없고, 주장 수준 멘션은 역할이 있다.
    CONSTRAINT ck_entity_mention_grain
        CHECK (
            (assertion_id IS NULL AND role_code IS NULL)
            OR
            (assertion_id IS NOT NULL AND role_code IS NOT NULL)
        ),
    CONSTRAINT ck_entity_mention_text
        CHECK (NULLIF(BTRIM(normalized_text), '') IS NOT NULL),
    CONSTRAINT ck_entity_mention_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

COMMENT ON TABLE entity_mention IS
'문서·주장에서 관측된 엔티티 표면 문자열과 해소 상태. 미해소 표면형의 정본 보존처이자 재해소 배치·미상장 조직 마스터 큐레이션의 입력이다. 확정 역할 링크는 여전히 assertion_argument·event_argument 가 소유한다.';

-- 재실행 멱등키 (grain 별 부분 유니크 — uq_explanation_result_published_grain 전례)
CREATE UNIQUE INDEX uq_entity_mention_document_grain
    ON entity_mention (document_id, normalized_text)
    WHERE assertion_id IS NULL;
CREATE UNIQUE INDEX uq_entity_mention_assertion_grain
    ON entity_mention (assertion_id, role_code, normalized_text)
    WHERE assertion_id IS NOT NULL;

-- 미해소 표면형 집계(마스터 큐레이션 큐): 상태별 최신순 스캔
CREATE INDEX ix_entity_mention_status_norm ON entity_mention (resolution_status, normalized_text);
CREATE INDEX ix_entity_mention_entity ON entity_mention (entity_id) WHERE entity_id IS NOT NULL;

-- ── FK (파일 말미 일괄 — V202607150001 스타일) ───────────────────────────────

-- 관계의 양끝은 마스터 삭제로부터 보호한다(assertion_argument·event_argument 전례).
ALTER TABLE entity_relation
    ADD CONSTRAINT fk_entity_relation_subject
    FOREIGN KEY (subject_entity_id)
    REFERENCES entity (entity_id)
    ON DELETE RESTRICT;

ALTER TABLE entity_relation
    ADD CONSTRAINT fk_entity_relation_object
    FOREIGN KEY (object_entity_id)
    REFERENCES entity (entity_id)
    ON DELETE RESTRICT;

ALTER TABLE entity_relation
    ADD CONSTRAINT fk_entity_relation_thread
    FOREIGN KEY (source_thread_id)
    REFERENCES event_thread (thread_id)
    ON DELETE RESTRICT;

ALTER TABLE entity_relation
    ADD CONSTRAINT fk_entity_relation_fact
    FOREIGN KEY (source_fact_id)
    REFERENCES disclosure_fact (fact_id)
    ON DELETE RESTRICT;

ALTER TABLE entity_alias
    ADD CONSTRAINT fk_entity_alias_entity
    FOREIGN KEY (entity_id)
    REFERENCES entity (entity_id)
    ON DELETE CASCADE;

ALTER TABLE entity_mention
    ADD CONSTRAINT fk_entity_mention_document
    FOREIGN KEY (document_id)
    REFERENCES document (document_id)
    ON DELETE CASCADE;

ALTER TABLE entity_mention
    ADD CONSTRAINT fk_entity_mention_assertion
    FOREIGN KEY (assertion_id)
    REFERENCES document_assertion (assertion_id)
    ON DELETE CASCADE;

ALTER TABLE entity_mention
    ADD CONSTRAINT fk_entity_mention_entity
    FOREIGN KEY (entity_id)
    REFERENCES entity (entity_id)
    ON DELETE RESTRICT;
