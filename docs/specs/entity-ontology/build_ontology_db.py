#!/usr/bin/env python3
"""엔티티 온톨로지 설계 데이터의 단일 소스 → 검토 산출물 생성기.

산출물 (같은 폴더):
  - ontology.sqlite : 정규화된 검토 DB (테이블 + 검토용 뷰)
  - graph.html      : 자체완결 그래프 뷰 (vis-network CDN, 데이터 인라인)

실행: python3 build_ontology_db.py
데이터를 고치려면 아래 상수를 고치고 재실행한다 — sqlite/html 직접 편집 금지.
문서 대응: cq-catalog.md · entity-shapes.md · relation-specs.md (프로즈 근거),
전수 구조 데이터는 이 파일이 정본.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── CQ 카탈로그 (cq-catalog.md §A~D) ─────────────────────────────────────────
# (id, group, question, consumer, source_ref, priority, status)
CQS = [
    ("EO-CQ-01", "해소", "표면형 문자열이 어느 캐노니컬 엔티티로 해소되는가", "해소기", "top_unresolved 수확", "P1", "NEEDS-DESIGN"),
    ("EO-CQ-02", "해소", "동명 표면형을 판별하거나 안전하게 포기하는가", "해소기", "normalize_news 동명 제외 전례", "P2", "SCHEMA-READY"),
    ("EO-CQ-03", "해소", "인물 멘션을 소속 회사로 전파할 수 있는가", "해소기", "cohort #16 (N=73)", "P2", "NEEDS-DESIGN"),
    ("EO-CQ-04", "해소", "브랜드·제품 멘션을 소유·생산 기업으로 전파할 수 있는가", "해소기", "cohort #21 #49", "P2", "NEEDS-DESIGN"),
    ("EO-CQ-05", "해소", "역할 슬롯의 kind 오접지를 차단하는가", "해소기", "발견⑤ 접지 오염", "P1", "NEEDS-DESIGN"),
    ("EO-CQ-06", "코호트", "비상장 자회사 사건을 상장 모회사로 귀속할 수 있는가", "코호트", "cohort #48", "P1", "NEEDS-FILL"),
    ("EO-CQ-07", "코호트", "고객사·공급사 1홉 코호트를 방향 있게 구성할 수 있는가", "코호트", "cohort #10 #43", "P1", "NEEDS-FILL"),
    ("EO-CQ-08", "코호트", "수출통제→공급사→고객사 2홉 전파가 가능한가", "코호트", "cohort #50", "P2", "NEEDS-FILL"),
    ("EO-CQ-09", "코호트", "주체가 대기업집단 계열인지 독립인지 구분하는가", "코호트", "cohort #75", "P2", "NEEDS-FILL"),
    ("EO-CQ-10", "코호트", "유니버스 마스터로 무사건 대조군을 만들 수 있는가", "코호트", "cohort #56", "P3", "NEEDS-FILL"),
    ("EO-CQ-11", "코호트", "섹터·테마 좌표로 코호트를 층화할 수 있는가", "코호트", "cohort #29 #42", "P1", "NEEDS-DESIGN"),
    ("EO-CQ-12", "코호트", "기관별 제재·인허가 이력을 집계할 수 있는가", "코호트", "cohort #19 #20 #44", "P1", "NEEDS-DESIGN"),
    ("EO-CQ-13", "코호트", "합병 양측·원고/피고 페어를 방향 있게 복원하는가", "코호트", "cohort #46 #47", "P2", "NEEDS-FILL"),
    ("EO-CQ-14", "피처", "이벤트 시점에 엔티티 상태를 조인할 수 있는가", "분석엔진", "발견⑧ entity_state", "P2", "NEEDS-FILL"),
    ("EO-CQ-15", "피처", "관세·통제 대상 품목→노출 기업을 계산할 수 있는가", "분석엔진", "cohort #28", "P3", "NEEDS-FILL"),
    ("EO-CQ-16", "콘솔", "기업 현재 프로필을 한 번에 조회할 수 있는가", "콘솔", "검토 화면", "P2", "NEEDS-DESIGN"),
    ("EO-CQ-17", "콘솔", "미해소 표면형을 빈도순으로 검토·승격할 수 있는가", "콘솔", "마스터 큐레이션 큐", "P3", "SCHEMA-READY"),
    ("EO-CQ-18", "콘솔", "관계·엔티티의 출처와 확신도를 추적할 수 있는가", "콘솔", "GLEIF validation 동형", "P3", "SCHEMA-READY"),
]

# ── 엔티티 타입 백본 (entity-shapes.md §1) ───────────────────────────────────
# (name, layer, subtype_value, ontoclean, identity_criteria, registration_gate, status, note)
ENTITY_TYPES = [
    ("COMPANY", "actor", "COMPANY", "+R +I +U", "dart_corp_code(상장·외감) / 정규화명+국적(잠정)", "식별 슬롯 1개 이상", "active", "회사 캐노니컬 ID는 actor — instrument 아님"),
    ("PERSON", "actor", "PERSON", "+R +I +U", "정규화명 + 소속 회사 복합(이름 단독 불가)", "ceo_of/officer_of ≥1 동반 등재만 허용", "active", "+I를 관계가 공급하는 유일 타입"),
    ("AUTHORITY", "actor", "GOVERNMENT|INSTITUTION", "+R +I +U", "정규화 기관명(전역 유일)", "별칭(약칭) 동시 등재 필수", "active", "발견⑤ 접지 오염의 직접 해소 대상"),
    ("BRAND", "concept", "BRAND", "+R +I", "(소유사, 정규화 브랜드명) 복합", "owns_brand 동반 등재", "active", "법인 아님 — schema.org Brand 정합"),
    ("PRODUCT_FAMILY", "concept", "PRODUCT_FAMILY", "+R +I", "(트리 경로, 정규화명)", "parent(BRAND) 필수", "active", "개념 트리 중간층"),
    ("PRODUCT", "concept", "PRODUCT", "+R +I", "(트리 경로, 정규화명)", "parent 또는 produces 동반", "active", "고아 concept 금지"),
    ("SECTOR", "concept", "SECTOR", "+R +I", "외부 분류 코드(KRX·GICS) 승계", "외부 코드 필수 — 자체 발명 금지", "active", "코호트 층화 좌표"),
    ("THEME", "concept", "THEME", "+R +I", "큐레이션 명명", "큐레이션 전용", "active", "산업분류와 혼동 금지"),
    ("EQUITY", "instrument", "EQUITY", "+R +I", "(market_code, ticker)", "issuer FK 필수", "active", "완비"),
    ("ETF", "instrument", "ETF", "+R +I", "(market_code, ticker)", "etf_profile 동반", "active", "완비"),
    ("RULE", "concept", "RULE", "+R", "미정", "후속 개정에서 정의", "deferred", "실측 0건(발견⑦⑧) — 데이터 없는 마스터 금지"),
    ("LOCATION", "concept", "LOCATION", "+R", "미정", "후속", "deferred", "동상"),
    ("HAZARD", "concept", "HAZARD", "+R", "미정", "후속", "deferred", "동상"),
    ("INDEX", "concept", "INDEX", "+R +I", "지수사업자 코드", "market_series 연동 후속", "deferred", "member_of의 대상 — 등재 게이트 후속"),
]

# ── 셰이프 슬롯 (entity-shapes.md §3) ────────────────────────────────────────
# (type, slot, lane, storage, cardinality, required, anchor, fill_source, status, rationale)
SHAPE_SLOTS = [
    ("COMPANY", "actor_type", "분류", "actor.actor_type CHECK", "1", 1, "-", "적재 코드", "ok", "rigid 백본"),
    ("COMPANY", "sector", "분류·파셋", "관계 in_sector로 위임", "0..1(1차)+테마N", 0, "Wikidata P452", "KRX·GICS 피드", "design", "EO-CQ-11 — 파셋이지 서브클래스 아님"),
    ("COMPANY", "dart_corp_code", "식별", "company_profile UNIQUE", "0..1", 0, "GLEIF RA-ID 대응", "OpenDART corpCode", "ok", "C-급 골드 조인키(cohort #60)"),
    ("COMPANY", "display_name", "식별보조", "entity.display_name", "1", 1, "GLEIF LegalName", "유니버스", "ok", "정규화명 — 동명 충돌 시 AMBIGUOUS"),
    ("COMPANY", "country_code", "본질", "actor.country_code", "0..1", 0, "ISO 3166", "유니버스", "ok", ""),
    ("COMPANY", "profile_as_of_date", "기술", "company_profile", "0..1", 0, "-", "적재", "ok", "저빈도 갱신 as_of 패턴"),
    ("COMPANY", "founded_date", "본질", "-", "-", 0, "GLEIF L1(ISO 8601)", "-", "deferred", "D-10: 앵커 있으나 CQ 없음"),
    ("COMPANY", "legal_form", "본질", "-", "-", 0, "ISO 20275", "-", "deferred", "D-10"),
    ("COMPANY", "is_listed", "파생", "뷰(issuer_of 존재)", "-", 0, "-", "파생", "derived", "EO-CQ-06·14 — 컬럼 이중 저장 금지"),
    ("PERSON", "actor_type", "분류", "actor.actor_type CHECK", "1", 1, "-", "적재 코드", "ok", "rigid 백본"),
    ("PERSON", "display_name", "식별(부분)", "entity.display_name", "1", 1, "-", "뉴스 추출 승인", "ok", "이름 단독 식별 불가 — 게이트가 보완"),
    ("PERSON", "registration_gate", "게이트", "적재 정책", "-", 1, "-", "-", "design", "관계 동반 등재만 — 오염 예방"),
    ("AUTHORITY", "actor_type", "분류", "actor.actor_type CHECK", "1", 1, "-", "적재 코드", "ok", "GOVERNMENT|INSTITUTION"),
    ("AUTHORITY", "display_name", "식별", "entity.display_name", "1", 1, "-", "큐레이션", "ok", "전역 유일 정규화 기관명"),
    ("AUTHORITY", "aliases", "식별", "entity_alias 필수 등재", "1..n", 1, "-", "큐레이션", "design", "EO-CQ-12 — 약칭(공정위) 세트"),
    ("BRAND", "concept_type", "분류", "concept.concept_type(CHECK 없음)", "1", 1, "schema.org Brand", "적재 코드", "design", "어휘 정본은 셰이프 명세"),
    ("BRAND", "identity", "식별", "(owner, 정규화명) 복합", "1", 1, "-", "큐레이션·추출 승인", "design", "브랜드명 전역 유일 아님"),
    ("PRODUCT", "concept_type", "분류", "concept.concept_type", "1", 1, "-", "적재 코드", "design", ""),
    ("PRODUCT", "parent_path", "분류·식별", "concept.parent_concept_id 트리", "0..1", 0, "-", "큐레이션", "design", "트리 경로가 식별의 일부"),
    ("SECTOR", "external_code", "식별", "concept + 코드 속성(후속)", "1", 1, "KRX 업종·GICS", "마스터 피드", "design", "자체 발명 금지"),
    ("EQUITY", "market_ticker", "식별", "instrument UNIQUE(market_code,ticker)", "1", 1, "MIC(ISO 10383)", "KRX", "ok", "완비"),
    ("EQUITY", "issuer", "본질", "equity_profile.issuer_actor_id FK", "1", 1, "-", "적재", "ok", "issuer_of 정본"),
    ("EQUITY", "isin", "식별(보조)", "-", "-", 0, "ISO 6166", "-", "deferred", "ADR-0027 — 필요 시 속성 확장"),
    ("ETF", "market_ticker", "식별", "instrument UNIQUE", "1", 1, "MIC", "KRX", "ok", "완비"),
]

# ── 관계 명세 (relation-specs.md) ────────────────────────────────────────────
# (code, layer, subj_type, obj_type, subj_role, obj_role, inverse_name, transitive,
#  cardinality, anchor, discriminant, time_semantics, fill_source, not_rule,
#  source_event_type, lifecycle_model, valid_from_rule, valid_to_rule, status, cohort_cases)
RELATIONS = [
    ("ceo_of", "REFERENCE", "PERSON", "COMPANY", None, None, "has_ceo", 0, "회사당 0..n(공동대표)",
     "Wikidata P169", "등기 대표이사 — 그 법인의 CEO(그룹 총수 아님)", "snapshot", "뉴스 승인·큐레이션·DART(후속)",
     "회장·총수 지위 자체", None, None, None, None, "active", "#16"),
    ("officer_of", "REFERENCE", "PERSON", "COMPANY", None, None, "has_officer", 0, "N:M",
     "FIBO 임원 관계군", "등기 임원(사내·사외이사·감사)", "snapshot", "뉴스 승인·큐레이션",
     "비등기 집행임원·직원", None, None, None, None, "active", "#16"),
    ("subsidiary_of", "REFERENCE", "COMPANY", "COMPANY", None, None, "has_subsidiary", 1, "자회사당 모회사 0..1",
     "GLEIF IsDirectlyConsolidatedBy · FIBO hasSubsidiary · schema.org parentOrganization · Wikidata P749",
     "연결회계 기준(지배력) — 지분율 수치는 공시 fact 참조", "snapshot", "큐레이션·DART 계열회사(후속)·공시",
     "단순 지분 보유(→has_stake)·동일 집단 소속(비지배)", None, None, None, None, "active", "#48 #75"),
    ("owns_brand", "REFERENCE", "COMPANY", "BRAND", None, None, "brand_owner", 0, "N:M",
     "schema.org Brand · Wikidata P127", "상표의 보유·운영 주체(라이선시 아님)", "snapshot", "큐레이션·뉴스 승인",
     "라이선스 사용권·유통권", None, None, None, None, "active", "#49"),
    ("produces", "REFERENCE", "COMPANY", "PRODUCT", None, None, "produced_by", 0, "N:M",
     "schema.org manufacturer(역) · Wikidata P1056", "자사 생산·판매 주력(현재 포트폴리오)", "snapshot", "큐레이션·뉴스 승인",
     "유통·리셀·판매 대행", None, None, None, None, "active", "#21 #49"),
    ("in_sector", "REFERENCE", "COMPANY", "SECTOR", None, None, "sector_members", 0, "1차 섹터 0..1 + 테마 N",
     "Wikidata P452 · schema.org isicV4 계열", "외부 분류체계(KRX·GICS) 승계 — 자체 판정 금지", "snapshot", "KRX 마스터 피드·큐레이션",
     "테마(투기적 묶음)와의 혼동", None, None, None, None, "active", "#29 #42"),
    ("owns", "EVENT", "COMPANY", "COMPANY", "ACQUIRER", "TARGET_COMPANY", "owned_by", 0, "관측 단위",
     "FIBO hasMajorityOwnedSubsidiary", "지배권 이전 완료(EFFECTIVE)", "effective-dated", "thread projection",
     "소수 지분(→has_stake)", "COMPANY.M_AND_A.ACQUISITION", "DEAL_LIFECYCLE", "EFFECTIVE 도달", "CANCELLED(성사 전 미개시)", "projection-후속", "#11 #12 #13 #46"),
    ("has_stake", "EVENT", "COMPANY", "COMPANY", "INVESTOR", "TARGET_COMPANY", "stake_held_by", 0, "관측 단위",
     "FIBO holdsVotingRightsIn 계열", "비지배 소수 지분", "effective-dated", "thread projection",
     "지배권 취득(→owns)", "COMPANY.INVESTMENT.STAKE_ACQUISITION", "DEAL_LIFECYCLE", "보고 EFFECTIVE_DATE", "EXIT predicate", "projection-후속", "#14"),
    ("supplies", "EVENT", "COMPANY", "COMPANY", "SUPPLIER", "CUSTOMER", "supplied_by", 0, "관측 단위",
     "FIBO 상거래 관계", "계약 성립(공시 우선)", "effective-dated", "thread+supply_contract_fact",
     "제휴(PARTNERSHIP — 대칭·기각)", "COMPANY.CONTRACT.SIGNING", "DEAL_LIFECYCLE", "계약 시작일", "계약 종료일", "projection-후속", "#9 #10 #43 #50 #77"),
    ("produces_event", "EVENT", "COMPANY", "PRODUCT", "ISSUER", "PRODUCT", "produced_by", 0, "관측 단위",
     "(참조층 produces와 코드 공유)", "출시·양산 도달", "effective-dated", "thread projection",
     "-", "COMPANY.PRODUCT.LAUNCH", "PRODUCT_TECH_LIFECYCLE", "EFFECTIVE_DATE·SHIPPING", "DISCONTINUED", "projection-후속", "#21"),
    ("certified_for", "EVENT", "COMPANY", "PRODUCT", "ISSUER", "PRODUCT", "certification_of", 0, "관측 단위",
     "-", "인증·인허가 발효", "effective-dated", "thread projection",
     "규제 제재(→REGULATORY_ACTION)", "COMPANY.PRODUCT.CERTIFICATION", "PRODUCT_TECH_LIFECYCLE", "EFFECTIVE_DATE", "REJECTED", "projection-후속", "#20 #80"),
    ("restricts", "EVENT", "AUTHORITY", "COMPANY", "AUTHORITY", "TARGET", "restricted_by", 0, "관측 단위",
     "-", "수출통제 발효", "effective-dated", "thread projection",
     "관세(→tariff_applies_to)", "POLICY.TRADE.EXPORT_CONTROL", "POLICY_LIFECYCLE", "EFFECTIVE", "LIFTED·EASE", "projection-후속", "#30 #50 #78"),
    ("tariff_applies_to", "EVENT", "AUTHORITY", "COMPANY", "AUTHORITY", "TARGET", "tariffed_by", 0, "관측 단위",
     "-", "관세 발효", "effective-dated", "thread projection",
     "수출통제(→restricts)", "POLICY.TRADE.TARIFF_CHANGE", "POLICY_LIFECYCLE", "EFFECTIVE", "REMOVE·REPEALED", "projection-후속", "#28"),
    ("sanctions", "EVENT", "AUTHORITY", "COMPANY", "AUTHORITY", "TARGET", "sanctioned_by", 0, "관측 단위",
     "-", "제재 발효", "effective-dated", "thread projection",
     "-", "POLICY.SANCTION.IMPOSITION", "POLICY_LIFECYCLE", "EFFECTIVE", "LIFT·REPEALED", "projection-후순위(데이터 0)", "발견⑦"),
    ("member_of", "EVENT", "EQUITY", "INDEX", "MEMBER", "INDEX", "index_members", 0, "관측 단위",
     "-", "지수 편입 발효", "effective-dated", "thread projection",
     "-", "MARKET_STRUCTURE.INDEX.INCLUSION", "MARKET_STRUCTURE_LIFECYCLE", "EFFECTIVE_DATE", "EXCLUSION 역이벤트·REVERSED", "projection-후속", "#25 #81"),
    ("issuer_of", "STATIC", "COMPANY", "EQUITY", None, None, "issued_by", 0, "1:N",
     "-", "발행 관계 — equity_profile 정본", "current", "load-instruments",
     "복제 적재 금지(조회 UNION)", None, None, None, None, "active", "#48 #56"),
    ("constituent_of", "STATIC", "EQUITY", "ETF", None, None, "holdings", 0, "N:M 시점별",
     "-", "구성종목 — etf_holding_snapshot 정본", "dated-snapshot", "load-etf-holdings",
     "평탄화 금지", None, None, None, None, "active", "발견⑨ 가격측 기질(#61 #63 #84 가동 전제)"),
]

# ── 게이트 결정 로그 (relation-specs.md §4) ──────────────────────────────────
DECISIONS = [
    ("D-01", "참조층 5종(ceo_of·officer_of·subsidiary_of·owns_brand·produces)", "수용", "EO-CQ-03·04·06·07·09·16, 표준 앵커 전건 존재"),
    ("D-02", "이벤트층 9종", "수용(projection 후속)", "thread 계약 승계 — 발명 0, 코호트 케이스 추적"),
    ("D-03", "정적 2종(issuer_of·constituent_of)", "수용(비저장)", "정본 테이블 존재 — 복제 금지"),
    ("D-04", "상태형 4종(operation·service·trading_status·regulates_or_rule_status)", "기각", "지속성 테스트 실패 — 이벤트 소관"),
    ("D-05", "PARTNERSHIP", "기각(보류)", "대칭 관계(owl:SymmetricProperty) — 단방향 모델 부적합"),
    ("D-06", "지분율·직함 상세의 관계 속성화", "기각", "관계≠이벤트 복제 — 수치는 공시 fact·이벤트 소관"),
    ("D-07", "in_sector 신설", "수용", "EO-CQ-11·cohort #29, 앵커 Wikidata P452, 판별식=외부 분류 승계"),
    ("D-08", "ultimate_parent_of", "유예", "subsidiary_of 전이 폐포로 파생 가능 — 별도 저장 불요"),
    ("D-09", "RULE·LOCATION·HAZARD·INDEX 마스터", "유예", "실측 0건(발견⑦⑧) — 데이터 없는 마스터는 오염 위험"),
    ("D-10", "설립일·legal_form·ISIN 속성", "유보", "앵커(GLEIF L1·ISO 20275·ISO 6166) 있으나 대응 CQ 없음"),
]

# ── CQ 추적 (cq_id, element_kind, element_key) ──────────────────────────────
CQ_TRACE = [
    ("EO-CQ-01", "infra", "entity_alias"),
    ("EO-CQ-02", "infra", "entity_alias.is_ambiguous"), ("EO-CQ-02", "infra", "entity_mention.AMBIGUOUS"),
    ("EO-CQ-03", "relation", "ceo_of"), ("EO-CQ-03", "relation", "officer_of"), ("EO-CQ-03", "type", "PERSON"),
    ("EO-CQ-04", "relation", "owns_brand"), ("EO-CQ-04", "relation", "produces"), ("EO-CQ-04", "type", "BRAND"), ("EO-CQ-04", "type", "PRODUCT"),
    ("EO-CQ-05", "infra", "role_kind_constraint"), ("EO-CQ-05", "type", "AUTHORITY"),
    ("EO-CQ-06", "relation", "subsidiary_of"), ("EO-CQ-06", "relation", "issuer_of"), ("EO-CQ-06", "slot", "COMPANY.is_listed"),
    ("EO-CQ-07", "relation", "supplies"),
    ("EO-CQ-08", "relation", "restricts"), ("EO-CQ-08", "relation", "supplies"),
    ("EO-CQ-09", "relation", "subsidiary_of"),
    ("EO-CQ-10", "type", "COMPANY"),
    ("EO-CQ-11", "relation", "in_sector"), ("EO-CQ-11", "type", "SECTOR"), ("EO-CQ-11", "type", "THEME"),
    ("EO-CQ-12", "type", "AUTHORITY"), ("EO-CQ-12", "slot", "AUTHORITY.aliases"),
    ("EO-CQ-13", "infra", "event_argument"),
    ("EO-CQ-14", "infra", "entity_state_join"), ("EO-CQ-14", "slot", "COMPANY.is_listed"),
    ("EO-CQ-15", "relation", "tariff_applies_to"), ("EO-CQ-15", "relation", "restricts"), ("EO-CQ-15", "type", "PRODUCT"),
    ("EO-CQ-16", "relation", "ceo_of"), ("EO-CQ-16", "relation", "officer_of"), ("EO-CQ-16", "relation", "subsidiary_of"),
    ("EO-CQ-16", "relation", "owns_brand"), ("EO-CQ-16", "relation", "produces"), ("EO-CQ-16", "relation", "issuer_of"),
    ("EO-CQ-17", "infra", "entity_mention"),
    ("EO-CQ-18", "infra", "provenance"),
]

# concept 트리 엣지 (그래프 전용 — 저장은 parent_concept_id)
TREE_EDGES = [("PRODUCT", "PRODUCT_FAMILY"), ("PRODUCT_FAMILY", "BRAND")]

DDL = """
CREATE TABLE cq (
    cq_id TEXT PRIMARY KEY, cq_group TEXT NOT NULL, question TEXT NOT NULL,
    consumer TEXT NOT NULL, source_ref TEXT NOT NULL, priority TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE entity_type (
    name TEXT PRIMARY KEY, layer TEXT NOT NULL, subtype_value TEXT NOT NULL, ontoclean TEXT NOT NULL,
    identity_criteria TEXT NOT NULL, registration_gate TEXT NOT NULL, status TEXT NOT NULL, note TEXT);
CREATE TABLE shape_slot (
    type_name TEXT NOT NULL REFERENCES entity_type(name), slot TEXT NOT NULL, lane TEXT NOT NULL,
    storage TEXT NOT NULL, cardinality TEXT NOT NULL, required INTEGER NOT NULL,
    anchor TEXT, fill_source TEXT, status TEXT NOT NULL, rationale TEXT,
    PRIMARY KEY (type_name, slot));
CREATE TABLE relation (
    code TEXT NOT NULL, layer TEXT NOT NULL,
    subject_type TEXT NOT NULL REFERENCES entity_type(name),
    object_type TEXT NOT NULL REFERENCES entity_type(name),
    subject_role TEXT, object_role TEXT, inverse_name TEXT, transitive INTEGER NOT NULL,
    cardinality TEXT, anchor TEXT, discriminant TEXT NOT NULL, time_semantics TEXT NOT NULL,
    fill_source TEXT, not_rule TEXT, source_event_type TEXT, lifecycle_model TEXT,
    valid_from_rule TEXT, valid_to_rule TEXT, status TEXT NOT NULL, cohort_cases TEXT,
    PRIMARY KEY (code, layer));
CREATE TABLE decision (
    decision_id TEXT PRIMARY KEY, subject TEXT NOT NULL, verdict TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE cq_trace (
    cq_id TEXT NOT NULL REFERENCES cq(cq_id), element_kind TEXT NOT NULL, element_key TEXT NOT NULL,
    PRIMARY KEY (cq_id, element_kind, element_key));

CREATE VIEW v_relation_review AS
    SELECT layer, code, subject_type || ' → ' || object_type AS edge,
           inverse_name, CASE transitive WHEN 1 THEN '✓' ELSE '' END AS transitive,
           cardinality, anchor, discriminant, time_semantics, fill_source, not_rule, status, cohort_cases
    FROM relation ORDER BY CASE layer WHEN 'REFERENCE' THEN 0 WHEN 'EVENT' THEN 1 ELSE 2 END, code;
CREATE VIEW v_type_shape AS
    SELECT s.type_name, t.status AS type_status, s.lane, s.slot, s.storage, s.cardinality,
           CASE s.required WHEN 1 THEN 'REQ' ELSE 'opt' END AS req, s.anchor, s.fill_source, s.status, s.rationale
    FROM shape_slot s JOIN entity_type t ON t.name = s.type_name
    ORDER BY s.type_name, CASE s.lane WHEN '분류' THEN 0 WHEN '분류·파셋' THEN 1 WHEN '분류·식별' THEN 1
        WHEN '식별' THEN 2 WHEN '식별보조' THEN 3 WHEN '식별(부분)' THEN 3 WHEN '식별(보조)' THEN 3
        WHEN '본질' THEN 4 WHEN '기술' THEN 5 WHEN '게이트' THEN 6 ELSE 7 END;
CREATE VIEW v_cq_coverage AS
    SELECT c.cq_id, c.priority, c.status, c.question,
           COUNT(t.element_key) AS n_elements, COALESCE(GROUP_CONCAT(t.element_key, ' · '), '(미추적!)') AS elements
    FROM cq c LEFT JOIN cq_trace t ON t.cq_id = c.cq_id
    GROUP BY c.cq_id ORDER BY c.cq_id;
CREATE VIEW v_untraced_relations AS
    -- G1 근거 규칙: EO-CQ 추적 ∨ 코호트 케이스 근거. 양쪽 다 없으면 어휘 자격 미달.
    SELECT r.layer, r.code FROM relation r
    WHERE (r.cohort_cases IS NULL OR r.cohort_cases IN ('', '-'))
      AND NOT EXISTS (SELECT 1 FROM cq_trace t WHERE t.element_kind = 'relation'
        AND (t.element_key = r.code OR (r.code = 'produces_event' AND t.element_key = 'produces')))
    ORDER BY r.layer, r.code;
CREATE VIEW v_open_items AS
    SELECT 'type' AS kind, name AS item, status, note AS detail FROM entity_type WHERE status <> 'active'
    UNION ALL
    SELECT 'slot', type_name || '.' || slot, status, rationale FROM shape_slot WHERE status IN ('deferred', 'design')
    UNION ALL
    SELECT 'relation', code, status, discriminant FROM relation WHERE status <> 'active'
    UNION ALL
    SELECT 'decision', decision_id || ' ' || subject, verdict, reason FROM decision WHERE verdict LIKE '유%'
    ORDER BY kind, item;
"""


def build_sqlite(path: Path) -> None:
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.executescript(DDL)
    con.executemany("INSERT INTO cq VALUES (?,?,?,?,?,?,?)", CQS)
    con.executemany("INSERT INTO entity_type VALUES (?,?,?,?,?,?,?,?)", ENTITY_TYPES)
    con.executemany("INSERT INTO shape_slot VALUES (?,?,?,?,?,?,?,?,?,?)", SHAPE_SLOTS)
    con.executemany("INSERT INTO relation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", RELATIONS)
    con.executemany("INSERT INTO decision VALUES (?,?,?,?)", DECISIONS)
    con.executemany("INSERT INTO cq_trace VALUES (?,?,?)", CQ_TRACE)
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()


LAYER_STYLE = {
    "REFERENCE": {"color": "#2b7de9", "dashes": False, "label": "참조(현재 지식)"},
    "EVENT": {"color": "#e07a29", "dashes": [6, 4], "label": "이벤트 파생"},
    "STATIC": {"color": "#8a8a8a", "dashes": [2, 3], "label": "정적(정본 테이블)"},
    "TREE": {"color": "#b58900", "dashes": [1, 2], "label": "개념 트리(parent_concept_id)"},
}
GROUP_STYLE = {
    "actor": "#dbeafe", "instrument": "#dcfce7", "concept": "#ffedd5",
}


def build_graph_html(path: Path) -> None:
    nodes, edges = [], []
    for name, layer, subtype, onto, ident, gate, status, note in ENTITY_TYPES:
        nodes.append({
            "id": name, "label": name, "group": layer,
            "shape": "box", "borderWidth": 1 if status == "active" else 1,
            "color": {"background": GROUP_STYLE[layer], "border": "#666" if status == "active" else "#bbb"},
            "font": {"color": "#222" if status == "active" else "#999"},
            "shapeProperties": {"borderDashes": status != "active"},
            "meta": {"layer": layer, "subtype": subtype, "ontoclean": onto, "identity": ident,
                      "gate": gate, "status": status, "note": note},
        })
    eid = 0
    for r in RELATIONS:
        code, layer = r[0], r[1]
        label = "produces" if code == "produces_event" else code
        style = LAYER_STYLE[layer]
        eid += 1
        edges.append({
            "id": f"e{eid}", "from": r[2], "to": r[3], "label": label, "arrows": "to",
            "color": {"color": style["color"]}, "dashes": style["dashes"], "font": {"size": 10, "align": "top"},
            "layer": layer,
            "meta": {"code": code, "layer": layer, "roles": f"{r[4] or '-'}→{r[5] or '-'}", "inverse": r[6],
                      "transitive": bool(r[7]), "cardinality": r[8], "anchor": r[9], "discriminant": r[10],
                      "time": r[11], "fill": r[12], "not": r[13], "event_type": r[14] or "-",
                      "valid": f"{r[16] or '-'} / {r[17] or '-'}", "status": r[18], "cases": r[19]},
        })
    for child, parent in TREE_EDGES:
        eid += 1
        style = LAYER_STYLE["TREE"]
        edges.append({"id": f"e{eid}", "from": child, "to": parent, "label": "parent", "arrows": "to",
                      "color": {"color": style["color"]}, "dashes": style["dashes"], "font": {"size": 9},
                      "layer": "TREE", "meta": {"code": "parent_concept_id", "layer": "TREE",
                      "discriminant": "개념 내 위계 — concept.parent_concept_id 트리(관계 테이블 금지)"}})

    data = {"nodes": nodes, "edges": edges,
            "legend": {k: v["label"] for k, v in LAYER_STYLE.items()}}
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    path.write_text(html, encoding="utf-8")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>edge 엔티티 온톨로지 그래프 (v1 · ALPHA-509)</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
 body{margin:0;font-family:'Segoe UI',sans-serif;display:flex;height:100vh}
 #net{flex:1;border-right:1px solid #ddd}
 #side{width:340px;padding:12px;overflow:auto;font-size:13px}
 h1{font-size:15px;margin:4px 0 8px} h2{font-size:13px;margin:10px 0 4px}
 .chip{display:inline-block;padding:1px 8px;border-radius:10px;margin:2px;font-size:11px;border:1px solid #ccc;cursor:pointer;user-select:none}
 .chip.off{opacity:.3}
 table{border-collapse:collapse;width:100%} td{border-top:1px solid #eee;padding:3px 4px;vertical-align:top}
 td:first-child{color:#777;white-space:nowrap;width:84px}
 #detail{background:#fafafa;border:1px solid #eee;border-radius:6px;padding:8px;min-height:120px}
 .legend-line{margin:2px 0;font-size:12px}
 .sw{display:inline-block;width:22px;height:3px;vertical-align:middle;margin-right:6px}
</style></head><body>
<div id="net"></div>
<div id="side">
 <h1>엔티티 온톨로지 v1</h1>
 <div>노드 = 엔티티 타입 (회색 점선 = 유예) · 엣지 = 관계. 클릭하면 상세.</div>
 <h2>레이어 필터</h2><div id="chips"></div>
 <h2>범례</h2><div id="legend"></div>
 <h2>상세</h2><div id="detail">노드나 엣지를 클릭하세요.</div>
 <h2>검토 동선</h2>
 <div>① 전체 조망(이 화면) → ② <code>ontology.sqlite</code> 뷰(v_relation_review·v_type_shape·v_cq_coverage·v_open_items) → ③ 스펙 문서 근거 확인.</div>
</div>
<script>
const DATA = __DATA__;
const nodes = new vis.DataSet(DATA.nodes);
const edges = new vis.DataSet(DATA.edges);
const net = new vis.Network(document.getElementById('net'), {nodes, edges}, {
  physics: {solver: 'forceAtlas2Based', forceAtlas2Based: {gravitationalConstant: -80, springLength: 140}, stabilization: {iterations: 250}},
  edges: {smooth: {type: 'dynamic'}, width: 1.6},
  nodes: {margin: 8, font: {size: 14}},
  interaction: {hover: true},
});
const layers = Object.keys(DATA.legend);
const chipBox = document.getElementById('chips');
const legendBox = document.getElementById('legend');
const active = Object.fromEntries(layers.map(l => [l, true]));
const LC = {REFERENCE:'#2b7de9', EVENT:'#e07a29', STATIC:'#8a8a8a', TREE:'#b58900'};
layers.forEach(l => {
  const c = document.createElement('span');
  c.className = 'chip'; c.textContent = DATA.legend[l]; c.style.borderColor = LC[l];
  c.onclick = () => { active[l] = !active[l]; c.classList.toggle('off', !active[l]); refresh(); };
  chipBox.appendChild(c);
  const line = document.createElement('div'); line.className = 'legend-line';
  line.innerHTML = `<span class="sw" style="background:${LC[l]}"></span>${DATA.legend[l]}`;
  legendBox.appendChild(line);
});
function refresh(){
  DATA.edges.forEach(e => edges.update({id: e.id, hidden: !active[e.layer]}));
}
function row(k, v){ return v ? `<tr><td>${k}</td><td>${v}</td></tr>` : ''; }
net.on('click', p => {
  const d = document.getElementById('detail');
  if (p.edges.length && !p.nodes.length) {
    const m = edges.get(p.edges[0]).meta || {};
    d.innerHTML = `<b>${m.code||''}</b> <span style="color:${LC[m.layer]||'#333'}">[${m.layer||''}]</span><table>`+
      row('roles', m.roles)+row('역명', m.inverse)+row('전이', m.transitive?'✓':'')+row('기수성', m.cardinality)+
      row('앵커', m.anchor)+row('판별식', m.discriminant)+row('시간', m.time)+row('소스', m.fill)+
      row('NOT', m['not'])+row('이벤트타입', m.event_type)+row('개시/마감', m.valid)+row('상태', m.status)+row('케이스', m.cases)+'</table>';
  } else if (p.nodes.length) {
    const m = nodes.get(p.nodes[0]).meta || {};
    d.innerHTML = `<b>${p.nodes[0]}</b> <span>[${m.layer}]</span><table>`+
      row('서브타입', m.subtype)+row('OntoClean', m.ontoclean)+row('식별 기준', m.identity)+
      row('등재 게이트', m.gate)+row('상태', m.status)+row('비고', m.note)+'</table>';
  } else { d.textContent = '노드나 엣지를 클릭하세요.'; }
});
</script></body></html>
"""


def main() -> None:
    build_sqlite(HERE / "ontology.sqlite")
    build_graph_html(HERE / "graph.html")
    con = sqlite3.connect(HERE / "ontology.sqlite")
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("cq", "entity_type", "shape_slot", "relation", "decision", "cq_trace")}
    con.close()
    print("built:", counts)


if __name__ == "__main__":
    main()
