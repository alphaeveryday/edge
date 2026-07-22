---
doc_type: contract
status: Draft
owner: data-platform
created: 2026-07-10
updated: 2026-07-10
related:
  - ../specs/data/entity-master.md
  - ../baseline/data-ingestion.md
---
# 엔티티 마스터 표준 형태 (계약)

분산돼 있던 KR 엔티티 참조 데이터를 하나의 **표준 형태**로 alphamale에 통합한 결과물의 정확한 형태다. 설계 근거·경계는 [엔티티 마스터 스펙](../specs/data/entity-master.md)이 소유한다. 이 문서는 레코드·아티팩트·빌드 계약만 고정한다.

각 엔티티는 두 역할을 한다: **해소 타깃**(뉴스 mention이 가리키는 대상)이자 **투영 노드**(이벤트를 종목으로 내려보내는 다리 — ISSUER=자신, COHORT/CONCEPT=`entity_member` 바스켓). instance(회사·사람)와 dimension(섹터·산업)은 테이블을 나누지 않고 `kind`로 판별한다. 설계 근거는 스펙이 소유한다.

## 표준 형태 정의 위치

| 무엇 | 경로 |
|---|---|
| 빌더 | `src/alphamale/reference/entity_master.py` (`build_from_concepts_db`, `write_sqlite`, CLI) |
| 레코드 JSON Schema | `src/alphamale/reference/entity_master.schema.json` |
| 예시 | `src/alphamale/reference/entity_master.example.yaml` |
| 적재 아티팩트 | `data/entity_master/entity_master.sqlite` + `entity_master_manifest.json` |

빌드: `python -m alphamale.reference.entity_master --concepts-db data/news/bigkinds/concepts.sqlite --out data/entity_master/entity_master.sqlite`. 이슈어 alias는 `--universe-parquet`로 `kr_universe_enriched.parquet`에서 보강한다(parquet 엔진 있을 때만, best-effort).

## `entity` — canonical 엔티티

| 항목 | 값 |
|---|---|
| 목적 | mention을 해소한 canonical 엔티티 1건 |
| grain | 엔티티 1건 (`entity_id`) |
| PK | `entity_id` |
| 생성자 | `reference/entity_master.py` |
| 소비자 | news 수집(mention 매핑), 뉴스 온톨로지(엔티티 링킹), 분석(코호트 바스켓) |

| 컬럼 | 타입 | null | 의미 |
|---|---|---|---|
| `entity_id` | TEXT | NOT NULL | namespaced canonical id. ISSUER=`KR_<ticker>`, 컨셉/코호트=`concept_id`(`th:`/`ind:`/`gics:`/`kr:` 접두) |
| `kind` | TEXT | NOT NULL | 7-kind taxonomy 중 하나 (현재 적재: ISSUER/COHORT/PRODUCT_OR_CONCEPT) |
| `canonical_name` | TEXT | NOT NULL | 대표명 |
| `persistence_key` | TEXT | NOT NULL | taxonomy 조인 키 값 (ISSUER=ticker, COHORT=group_key, CONCEPT=concept_id) |
| `ticker` | TEXT | NULL | ISSUER의 zero-padded 종목코드 |
| `concept_id` | TEXT | NULL | 컨셉/코호트 식별자 |
| `group_key` | TEXT | NULL | COHORT 바스켓 키(=concept_id); PRODUCT_OR_CONCEPT는 NULL |
| `source_kind` | TEXT | NULL | 소스 native kind (`stock`/`news_theme`/`gics_sector`/`gics_industry`/`sector_kr`) — 무손실 보존 |
| `source` | TEXT | NOT NULL | 의미론적 데이터셋 provenance: `kr_universe`(ISSUER) · `gics`/`llm`(COHORT) · `news+llm`(CONCEPT). 물리 소스(`kr_universe` parquet·`news_concepts`)는 아래 근거/출처가 소유하며 데이터 값으로 쓰지 않는다 |
| `confidence_rule` | TEXT | NULL | taxonomy 신뢰 규칙 문자열 |
| `as_of`, `data_version` | TEXT | NOT NULL | PIT 재현 metadata |
| 인덱스 | | | `ix_entity_kind(kind)` |

`market`·`exchange` 등 종목 속성은 이 테이블에 두지 않는다 — `ticker`로 종목코드 매핑(security/universe master)에 조인해 얻는다. entity master는 정체성·해소·투영만 소유한다.

## `entity_alias` — 별칭

| 항목 | 값 |
|---|---|
| 목적 | 엔티티 1건의 별칭 1개 |
| grain | `(entity_id, alias)` |
| FK | `entity_id` → `entity.entity_id` (logical) |
| 인덱스 | `ix_alias_entity(entity_id)` |

| 컬럼 | 타입 | null | 의미 |
|---|---|---|---|
| `entity_id` | TEXT | NOT NULL | 소유 엔티티 |
| `alias` | TEXT | NOT NULL | 별칭 표기 |
| `normalized` | TEXT | NULL | 정규화형 |
| `origin` | TEXT | NULL | 별칭 출처(`legacy_json`, `kr_universe:<col>` 등) |

현재 적재는 컨셉 별칭(`news_concept_aliases`, 909행)만 채운다. **이슈어 별칭은 `kr_universe_enriched.parquet` 보강 단계에서 채워지며, parquet 엔진이 없으면 gap으로 남는다**(0으로 대체 금지).

## `entity_member` — 코호트/컨셉 바스켓

| 항목 | 값 |
|---|---|
| 목적 | 코호트/컨셉의 구성 종목 1건 |
| grain | `(entity_id, member_ticker)` |
| FK | `entity_id` → `entity.entity_id`; `member_ticker` → ISSUER `entity.ticker` (logical) |
| 인덱스 | `ix_member_entity(entity_id)` |

| 컬럼 | 타입 | null | 의미 |
|---|---|---|---|
| `entity_id` | TEXT | NOT NULL | 코호트/컨셉 |
| `member_ticker` | TEXT | NULL | 구성 종목코드 |
| `member_name` | TEXT | NULL | 구성 종목명 |
| `weight` | REAL | NULL | 바스켓 가중 |
| `source` | TEXT | NULL | 멤버십 출처 |

## kind 매핑 규칙

소스 native kind → taxonomy kind. `source_kind`로 native 값을 무손실 보존하므로 매핑은 재조정 가능하다.

| 소스 | native kind | → taxonomy kind |
|---|---|---|
| KR universe (`kr_universe_enriched.parquet`) | 상장 종목 | `ISSUER` |
| `news_concepts` | `gics_sector`, `gics_industry`, `sector_kr` | `COHORT` |
| `news_concepts` | `news_theme` | `PRODUCT_OR_CONCEPT` |

## 현재 커버리지와 gap

| 항목 | 값 |
|---|---|
| `entity` | 3,708 (ISSUER 2,876 · COHORT 208 · PRODUCT_OR_CONCEPT 624) |
| `entity_alias` | 909 (컨셉측; 이슈어 alias는 parquet 보강 deferred) |
| `entity_member` | 6,533 |
| 미적재 kind | `COMPANY_ENTITY`(비상장 상대방), `AUTHORITY_OR_RULE`, `LOCATION_OR_HAZARD`, `INDEX_OR_EXCHANGE` — canonical master 없음(스펙 backlog) |

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| 소스 | `kr_universe_enriched.parquet`(ISSUER) + `concepts.sqlite`의 `news_concepts`·`news_concept_aliases`·`news_concept_members`(CONCEPT/COHORT) | 통합 입력. 구 `graph_nodes`는 강등된 graph substrate(현행 빌더가 ISSUER를 `graph_nodes`(stock) 미러로 읽는 것은 전환 예정 잔재) |
| taxonomy | `src/alphamale/events/ontology/resources/entity_mapping_contract_v0_1.yaml` | 7 kinds, persistence key, 신뢰 규칙 |
| 이슈어 alias 소스 | `data/news/bigkinds/kr_universe_enriched.parquet` | parquet 보강 입력 |
| 빌더 | `src/alphamale/reference/entity_master.py` | 통합·적재 실행 |
