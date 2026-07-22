---
doc_type: design
status: Draft
owner: data-platform
created: 2026-07-10
updated: 2026-07-11
related:
  - ../../baseline/data-ingestion.md
  - ../../baseline/analysis-engine-design.md
  - ../../baseline/analysis-engine-design.md
---
# 엔티티 마스터·해소 레이어

## Summary

mention(회사명·제품·컨셉·기관·지역·지수)을 canonical 엔티티(ticker·concept_id·group_key·정규화 문자열)로 해소하는 **참조 데이터와 결정 규칙**을 고정한다. News ingest와 Event ontology가 공유하는 입력 레이어이며, 소비자 문서가 이 계약을 참조만 한다.

경계: 이 문서는 **마스터·taxonomy·정규화기·canonical 브리지 계약**을 소유한다. 기사별 mention row 생성(`news_document_entity_link`/`news_document_concept_link` 등)은 [Data Ingestion 디자인](../../baseline/data-ingestion.md)이, canonical event 엔티티 링킹은 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md)이 이 레이어를 소비해 수행한다.

표준 형태: 위 분산 자산을 하나의 표준 레코드로 통합한 결과가 alphamale에 적재돼 있다 — 아티팩트 `data/entity_master/entity_master.sqlite`, 빌더 `src/alphamale/reference/entity_master.py`. 레코드·아티팩트의 정확한 형태는 [엔티티 마스터 표준 형태 계약](../../reference/entity-master.md)이 소유한다.

## Context

데이터 이름은 요구사항이다([문서 작성 규칙](../../../README.md)의 데이터 계약 원칙). 아래 경로·행수는 원본 개발 저장소의 **현행 lineage 증거**이며 물리 배치를 canonical로 선언하지 않는다. 대부분 이미 빌드돼 있고(`빌드됨`), 미구현은 `deferred 계약`/`설계·파일럿`으로 표기한다.

## Problem

엔티티 해소가 문서·spec마다 흩어져 있으면 같은 mention이 곳마다 다르게 매핑되고, 무엇이 결정론적으로 마스터돼 있고 무엇이 아직 문자열-only인지 판단할 수 없다.

## Goals

- 7종 엔티티 taxonomy와 각 종의 persistence key·신뢰 규칙을 한곳에 고정한다.
- 현행 마스터 아티팩트(노드·컨셉 카탈로그·유니버스·정규화기)의 lineage와 상태를 명시한다.
- canonical 엔티티 브리지 계약과 아직 마스터되지 않은 영역(backlog)을 구분한다.

## Non-goals

- 기사별 mention/링크 row 생성 로직 (news-ingestion 소유).
- canonical event 조립·엔티티 역할 배정 (news-ontology 소유).
- 물리 스키마·인덱스 (계약 문서 소유).

## 현행 자산 인벤토리

| 자산 | 현행 lineage (원본 개발 저장소) | 상태 |
|---|---|---|
| 엔티티 분류 taxonomy (7 kinds) | `src/alphamale/events/ontology/resources/entity_mapping_contract_v0_1.yaml` | 빌드됨 |
| 엔티티 노드 레지스트리 | ISSUER ← KR universe(`kr_universe_enriched.parquet`), 컨셉/코호트 ← `news_concepts`. 구 `graph_nodes`는 강등된 graph substrate의 미러이며 canonical 소스 아님 — 현행 빌더는 편의상 `graph_nodes`(stock)를 읽지만 universe 직접 읽기로 전환 예정 | 빌드됨 |
| 컨셉/테마 카탈로그 | 같은 db → `news_concepts`(832), `news_concept_aliases`(909), `news_concept_members`(6,533) | 빌드됨 |
| 기사↔티커 링크 | 같은 db → `news_article_links`(50k+), `news_ticker_monthly`(50k+) | 빌드됨 |
| 상장사 alias→ticker 유니버스 | `data/processed/news/kr_universe_enriched.parquet` (빌더와 taxonomy lineage 모두 `src/alphamale/news/universe/kr.py` 가 소유) | 빌드됨 |
| 정규화기 | `src/alphamale/graph/normalize.py` (`product_to_concept`, `company_to_ticker`) | 빌드됨 |
| canonical 엔티티 브리지(계약) | [Data Ingestion 디자인](../../baseline/data-ingestion.md) → `news_document_entity_link (document_id, entity_id)` | deferred 계약 |
| 글로벌 entity master 표준표 | 형제 repo `financial_event_engine_spec_v1/…/pilot/entity_map.json`(2,312개, `US_NVDA→[NVIDIA,…]`), `reference/entity_master.example.yaml`, `schemas/entity_master.schema.json` | 설계·파일럿(외부 repo, 본 저장소에 없음) |
| 표준 형태 통합 마스터 | `data/entity_master/entity_master.sqlite`(+manifest), 빌더 `src/alphamale/reference/entity_master.py`, 계약 [contracts/entity-master.md](../../reference/entity-master.md) | 빌드됨 (3,708 엔티티) |

## 엔티티 taxonomy (7 kinds)

`entity_mapping_contract_v0_1.yaml` 기준. persistence key가 해소 결과의 저장 축이다.

| kind | 현재 매핑 | persistence_key | 신뢰 규칙 | 쓰임 |
|---|---|---|---|---|
| `ISSUER` | KRX ticker (alias_map/`news_document_entity_link`) | `ticker` | 결정론적 full/curated alias, 또는 short alias + NER ORG 확인 | 기업 이벤트 주역, 시총·유동성 조인, 섹터 스필오버 |
| `COMPANY_ENTITY` | 상장사는 ticker, 아니면 정규화 org 문자열(미해소 상대방) | `ticker_or_normalized_name` | 상장사는 결정론적; 비상장 상대방은 org master 대기 | 인수·피인수·고객·공급사·파트너 역할 |
| `PRODUCT_OR_CONCEPT` | `product_to_concept`의 concept_id | `concept_id` | exact 또는 유일 best-length contains; 모호하면 miss | 제품 출시, 수요·공급·기술표준, 상품 스필오버 |
| `COHORT` | `sector_map` group_key | `group_key` | title+lead 최장 키워드; 산업군은 basket 보유, 매크로군은 없음 | 단일 발행사 없는 기사, 피어 섹터 방향, 테마·매크로 전파 |
| `AUTHORITY_OR_RULE` | 원문 정규화 문자열 | `normalized_authority_or_rule` | source-backed mention; canonical 규제·규칙 master 없음 | 정책·법원·규제·매크로 당국 역할 |
| `LOCATION_OR_HAZARD` | 원문 정규화 지역/재해 문자열 | `normalized_location_or_hazard` | source-backed mention only; 자산-위치 노출 조인 없음 | 재해·분쟁·보건·기상 등 외생 이벤트 |
| `INDEX_OR_EXCHANGE` | 원문 정규화 지수/거래소 문자열 | `normalized_index_or_exchange` | source-backed mention; 구성·플로우 truth는 provider feed 대기 | 지수 편입·편출, 거래정지, 거래소 장애 |

### 엔티티의 두 역할 — 왜 코호트·산업분류도 엔티티인가

각 엔티티는 (1) **해소 타깃**(뉴스 mention이 가리키는 대상)과 (2) **투영 노드**(이벤트를 종목으로 내려보내는 다리)를 겸한다. 코호트(섹터·산업·테마)에 id를 두는 이유:

- 단일 발행사를 안 가리키는 기사("2차전지株 일제히 급등")를 해소할 대상이 필요하다 — 없으면 미아가 된다.
- 코호트의 `entity_member` 바스켓이 테마·섹터 이벤트를 구성종목으로 투영한다(스필오버). ETF 제품에서 "테마 급등 → ETF 구성종목 기여"를 잇는 다리다.

instance(회사·사람)와 dimension(섹터·산업)은 테이블을 나누지 않고 `kind`로 판별한다 — 해소·투영을 단일 메커니즘으로 유지하기 위함. instance는 `ticker`, dimension은 `group_key`+바스켓이 판별 표지다. 종목 속성(market·exchange)은 entity master에 두지 않고 `ticker`로 security/universe master에 조인한다.

## 아직 마스터되지 않은 영역 (backlog)

taxonomy의 `future_entity_backlog` — 현재 문자열-only이거나 coarse해서 canonical master가 없는 지점이다. 무엇을 원인 후보로 쓸 수 있고 무엇을 못 쓰는지의 경계.

| id | 왜 필요한가 |
|---|---|
| `unlisted_organization_master` | 비KRX 상대방(고객·공급사)이 문자열로 남아 중복 엔티티 dedup 불가 |
| `product_revenue_concept_graph` | 제품 컨셉이 발행사별 매출 노출과 연결돼야 이벤트 영향 가중 가능 |
| `supplier_customer_network` | 섹터·테마 스필오버가 coarse; 직접 상하류 링크는 라이선스 관계 그래프 필요 |
| `official_policy_rule_master` | 정책·규칙 식별자와 lifecycle이 뉴스 문자열이 아닌 canonical 당국 캘린더 필요 |
| `geospatial_asset_registry` | 재해·분쟁·보건 이벤트는 텍스트 지역이 아니라 자산-위치 노출 필요 |
| `index_constituent_flow_model` | 지수·거래소 이벤트는 공식 구성 파일·패시브 AUM·장중 플로우 타이밍 필요 |
| `person_master` | 임원·애널리스트·정치인 등 개인이 이벤트 주체(CEO 사임, 애널리스트 콜)가 될 수 있으나 현재 PERSON kind가 없다(`AUTHORITY_OR_RULE`는 기관, 개인 아님). 소스 확보 시 8번째 kind로 승격 |

## 대안

| 대안 | 판단 |
|---|---|
| 글로벌 entity master 표준표(형제 repo 파일럿) 즉시 채택 | US 중심 2,312개 파일럿이라 KR 유니버스 커버리지·정합 검증 선행 필요. Open question 1 |
| taxonomy를 spec마다 중복 기술 | 같은 mention이 곳마다 다르게 매핑됨. 본 문서로 단일화 |

## 위험과 실패 처리

- **문자열-only 엔티티 오귀속**: `AUTHORITY_OR_RULE`·`LOCATION_OR_HAZARD`·`INDEX_OR_EXCHANGE`는 source-backed 문자열이라 dedup·노출 조인이 없다. 원인 후보로 쓸 때 신뢰도를 낮추고 backlog 미충족을 명시한다.
- **short alias 오매칭**: NER ORG 확인 없는 short alias는 링크하지 않는다(taxonomy 신뢰 규칙).
- **universe 빌더 경로 불일치**: 인벤토리에 구 legacy lineage와 현재 packaged lineage가 있어 물리화 시 canonical 빌더를 계약 문서에서 확정해야 한다.

## Open questions

1. 글로벌 entity master 표준표(외부 파일럿)를 프로덕션 master로 승격할지, KR 로컬 마스터와 어떻게 정합할지.
2. `unlisted_organization_master` 도입 시 `COMPANY_ENTITY`의 persistence key 전환 경로.
3. KR universe·concept 카탈로그의 갱신 주기와 as-of 재현 계약.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| taxonomy 계약 | `src/alphamale/events/ontology/resources/entity_mapping_contract_v0_1.yaml` | 7 kinds, persistence key, 신뢰 규칙, source_assets, future backlog |
| 정규화기 | `src/alphamale/graph/normalize.py` (`product_to_concept`, `company_to_ticker`) | concept/company 해소 로직 |
| 유니버스 빌더 | `src/alphamale/news/universe/kr.py` | 상장사 alias→ticker 유니버스 lineage |
| mention linker | `src/alphamale/news/linking/bigkinds.py`; `src/alphamale/news/linking/matching.py`; `src/alphamale/news/classification/sector_map.py` | mention·cohort 생성(소비자 = [Data Ingestion 디자인](../../baseline/data-ingestion.md)) |
| canonical 브리지 소비자 | [Data Ingestion 디자인](../../baseline/data-ingestion.md), [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) | `news_document_entity_link` 계약 소비 |
