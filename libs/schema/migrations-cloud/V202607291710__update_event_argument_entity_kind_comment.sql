-- event_argument.entity_kind 주석 갱신 — 종별 어휘에 PERSON 추가.
--
-- V202607242020 이 단 주석은 종별 7종을 열거했다. 그 열거는 실체 종별 계약
-- (edge_ontology resources/entity/entity_kinds_v0_1.yaml)을 옮겨 적은 것인데, 계약에 PERSON 이 추가되면서
-- 주석만 뒤처졌다. CHECK 제약이 아니라 주석이라 데이터에는 영향이 없지만, 스키마를 읽는
-- 사람에게 거짓말을 하므로 맞춘다.
--
-- PERSON 을 넣은 이유: 내부자거래·임원변경·소송 당사자의 identity 역할이 인물이다.
-- actor.actor_type 에 이미 'PERSON' 이 있어(V202607150001 ck_actor_type) 저장 자리는
-- 있었는데, 역할→종별 표가 없어 코드가 종별을 못 정하고 있었다.

COMMENT ON COLUMN event_argument.entity_kind IS
'엔티티 종별(ISSUER·COMPANY_ENTITY·PERSON·PRODUCT_OR_CONCEPT·COHORT·AUTHORITY_OR_RULE·LOCATION_OR_HAZARD·INDEX_OR_EXCHANGE) — 역할→종별 제약 검증 기질. 정본은 edge_ontology 관계층 role_bindings_v0_1.yaml 의 role_kinds.';
