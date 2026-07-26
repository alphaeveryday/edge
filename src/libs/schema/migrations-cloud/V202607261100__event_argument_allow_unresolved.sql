-- ALPHA-563: event_argument 미해소 참여자 보존 (expand — 기존 writer 무영향)
--
-- 온톨로지 계약에서 참여자는 **추출된 사실**이고 엔티티 해소는 별도 레인이다
-- (news-extraction-runbook-v4 레인 B: 추출 / "엔티티 해상은 코드 소유"). 그런데 물리
-- 스키마가 entity_id 를 NOT NULL 로 두고 **PK 에 포함**해, 마스터에 없는 참여자(비상장
-- 고객사·기관·해외기업)는 행 자체가 적재되지 못하고 버려졌다. 실측: 추출 2.4~2.8건/이벤트
-- 대비 적재 0.95건/이벤트, 주간 `unresolved` 카운터 4,296이 그 손실분이다. 스키마 제약이
-- 사실을 지우는 형태라 완화한다 — modality_code(V202607150003)·etf_type(V202607200001)과
-- 같은 계열의 정정이다.
--
-- 새 키 형태는 **형제 테이블 assertion_argument 와 동일**하게 맞춘다(같은 grain 개념의
-- 참여자 표): 서로게이트 identity PK + 자연키 UNIQUE. 자연키에 NULL 이 들어가면 Postgres
-- 는 그 행들을 서로 구분되는 것으로 보므로(NULL != NULL), 같은 역할의 미해소 참여자 둘
-- (고객사 A사·B사)이 각각 남는다 — 그게 이 변경이 원하는 동작이다.
--
-- **확장 전용**: 기존 writer 의 INSERT(컬럼 목록·`ON CONFLICT (source_event_id, role_code,
-- entity_id)`)는 그대로 동작한다 — identity 컬럼은 자동 채움이고 ON CONFLICT 는 아래
-- uq_event_argument 를 그대로 추론한다. 따라서 이 마이그레이션은 코드 배포와 무관하게
-- 먼저 적용할 수 있다(libs/schema/README "확장 마이그레이션 PR 선머지" 규율).
--
-- reader 영향 없음: 분석엔진은 이미 LEFT JOIN instrument 로 읽고(eventstore.py), 종목
-- 선별 EXISTS 는 접지된 참여자만 봐야 맞다 — 미해소는 보유 종목을 접지하지 못한다.

-- 1) 서로게이트 PK 도입 — 자연키에서 entity_id 를 빼려면 다른 PK 가 있어야 한다.
ALTER TABLE event_argument
    ADD COLUMN event_argument_id BIGINT GENERATED ALWAYS AS IDENTITY;

ALTER TABLE event_argument DROP CONSTRAINT event_argument_pkey;
ALTER TABLE event_argument ADD CONSTRAINT event_argument_pkey
    PRIMARY KEY (event_argument_id);

-- 2) 자연키는 UNIQUE 로 남긴다 — 기존 writer 의 ON CONFLICT 추론 대상이자 멱등 축.
ALTER TABLE event_argument ADD CONSTRAINT uq_event_argument
    UNIQUE (source_event_id, role_code, entity_id);

-- 3) 미해소 허용. FK(fk_event_argument_entity)는 그대로 둔다 — NULL 은 FK 를 통과하고,
--    값이 있으면 여전히 마스터에 있어야 한다.
ALTER TABLE event_argument ALTER COLUMN entity_id DROP NOT NULL;

-- 4) 유령 행 금지 — 접지도 표면형도 없는 참여자는 근거가 0이라 적재 의미가 없다.
ALTER TABLE event_argument ADD CONSTRAINT ck_event_argument_grounding
    CHECK (entity_id IS NOT NULL OR mention_text IS NOT NULL);

COMMENT ON COLUMN event_argument.event_argument_id IS
'서로게이트 키 — 자연키(source_event_id, role_code, entity_id)는 UNIQUE 로 남는다. assertion_argument 와 같은 관례.';
COMMENT ON COLUMN event_argument.entity_id IS
'엔티티 마스터 접지. NULL = 미해소(추출은 됐으나 마스터에 없음) — 표면형은 mention_text 에 남고, 접지 질의(엔진 선별)에서는 제외된다. 같은 역할의 미해소 참여자 복수는 NULL 이 서로 구분되어 각각 적재된다.';
COMMENT ON TABLE event_argument IS
'여러 문서 주장을 조립한 뒤 소스 이벤트에서 확정한 참여자 역할. 엔티티 접지는 선택(NULL=미해소) — 참여자는 추출된 사실이고 해소는 별도 레인이다.';
