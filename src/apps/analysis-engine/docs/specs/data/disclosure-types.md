---
doc_type: design
status: Draft
owner: engineering
created: 2026-07-08
updated: 2026-07-10
related:
  - ../../baseline/data-ingestion.md
  - ../../baseline/analysis-engine-design.md
  - entity-master.md
---
# 공시 타입 카탈로그

> **범위 메모:** 본문에 적힌 코드·데이터 경로는 현 저장소의 lineage 증거다. D0–D2a 운영 흐름, parser 분기, 적재 순서는 [Data Ingestion 디자인](../../baseline/data-ingestion.md)이 소유한다. D3 이후 fact·bridge 단계는 본 문서와 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) 소관이다.

## Summary

이 문서는 DART 공시 계층에서 오래 유지돼야 하는 **타입 vocabulary**만 분리해 고정한다. 범위는 세 가지다.

1. `dart_documents.doc_type` 허용 enum과 각 타입의 의미
2. 현재 정규화되는 공시 사실 타입(`supply_contract`, `business_segments`)의 **타입 수준 shape**
3. 공시 사실이 downstream evidence(수치·노출)로 넘어가는 방식, 그리고 issuer를 증권 축으로 고정하는 bridge 개념. (관계 그래프 투영은 [관계 그래프 draft](../../proposals/0002-relationship-graph.md) 소유.)

결론은 다음과 같다.

- 현재 disclosure의 **정규화 우선 타입**은 `supply_contract`, `business_segments` 두 가지다.
- `major_shareholders`, `other_investments`, `listing_products`, `generic`은 **parsed lake allowlist**에는 들어가지만, 이 문서 기준으로는 아직 정규화 사실·그래프 투영의 current 계약이 아니다.
- 공시 사실의 관계 그래프 투영(`supplies`/`produces` 등)은 복잡성 감축으로 [관계 그래프 draft](../../proposals/0002-relationship-graph.md)로 강등했다. 이 문서는 공시 사실을 **수치·노출 evidence**로만 current로 유지한다.
- issuer 동일성은 filing snapshot의 `corp_code`/`stock_code`가 아니라 `dart_corp_security_map`이 제공하는 **issuer-security bridge**를 통해 `(market, ticker, kind_stock_code)` 축으로 고정한다.

## Context

[문서 작성 규칙](../../../README.md)의 데이터 계약 원칙에 따라, 여기서 정의하는 이름은 물리 테이블 설명이 아니라 **이런 grain과 의미의 데이터가 존재해야 한다**는 logical 계약이다. 이제 raw requirement와 parsed lake까지의 운영 경계(D0–D2a)는 [Data Ingestion 디자인](../../baseline/data-ingestion.md)이 소유하고, 정규화 사실·graph/evidence bridge(D3 이후)는 본 문서와 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) 소관이며, 이 문서는 공시 타입 vocabulary만 분리해 남긴다.

- `doc_type` enum 중 무엇이 current normalization 대상인가?
- parser output이 어떤 **사실 타입**으로 승격되는가?
- issuer와 graph relation이 어떤 축과 의미로 downstream에 전달되는가?

이 문서는 그 질문에만 답하는 카탈로그다. 운영 단계 설명은 복사하지 않고 [Data Ingestion 디자인](../../baseline/data-ingestion.md)으로 링크한다.

## Problem

공시 spec가 stage map과 field 나열 중심으로 남아 있으면 타입 수준의 결정을 유지하기 어렵다.

1. 같은 `doc_type` enum이라도 **allowed**인지 **current normalized**인지 구분이 흐려진다.
2. `supply_contract`와 `business_segments`가 downstream에서 무엇을 뜻하는지보다, parser가 읽은 개별 필드 설명이 더 앞에 나오기 쉽다.
3. `corp_code`, filing-level `ticker`, canonical `(market, ticker)`가 섞여 보이면 issuer 동일성 규칙이 문서마다 달라질 수 있다.
4. 그래프 relation도 `produces`, `supplies` 같은 의미 타입과 store-level allowlist가 분리되지 않으면, 미래 관계 타입을 current disclosure output으로 오해하게 된다.

## Goals

- `dart_documents`가 허용하는 공시 문서 타입 enum을 한곳에서 고정한다.
- 현재 정규화되는 공시 사실 타입의 grain, 의미, downstream 질문을 타입 수준에서 설명한다.
- issuer-security bridge와 graph relation type의 의미를 current 기준으로 명확히 남긴다.
- current/allowlist/deferred를 구분해 후속 문서가 같은 vocabulary를 재사용하게 한다.

## Non-goals

- D0–D2a 단계, 적재 순서, parser 라우팅, 재처리 운영 로직 재서술 (D3 이후 fact·bridge 단계는 본 문서와 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) 소관)
- column/type/index와 같은 물리 스키마 정의
- `document_assertion`, `event_evidence`, `canonical_event`의 전체 계약 재정의
- 뉴스 온톨로지처럼 독립적인 `event_type_id` taxonomy를 여기서 소유하는 일

## 공시 문서 타입 카탈로그

`doc_type`은 parsed lake에서 “이 문서를 어떤 의미로 읽을 수 있는가”를 나타내는 **문서 해석 타입**이다. grain은 모두 `(rcept_no, doc_type)`이지만, downstream 의미와 정규화 여부는 다르다.

| `doc_type` | 상태 | 의미 | 현재 downstream 계약 |
|---|---|---|---|
| `supply_contract` | `current` | 단일판매·공급계약 공시. issuer가 누구에게 어떤 계약을 맺었고, 금액·매출비중·기간이 무엇인지 설명하는 계약 사실 문서 | `SupplyContractFact`로 정규화되어 계약금액·매출비중 **수치·노출 evidence**의 근거가 된다(관계 엣지 투영은 [관계 그래프 draft](../../proposals/0002-relationship-graph.md) 강등) |
| `business_segments` | `current` | 사업보고서 계열에서 issuer의 사업부문/제품군별 매출 구성과 비중을 설명하는 구성 사실 문서 | `BusinessSegmentFact`로 정규화되어 사업부문 매출비중 **노출·중요도 evidence**의 근거가 된다(관계 엣지 투영은 draft 강등) |
| `major_shareholders` | `current`(allowlist) / 정규화 deferred | OpenDART `hyslrSttus` 계열 대주주 현황 문서 | parsed payload 보존까지만 current로 관찰된다. 본 문서 기준 정규화 사실·graph projection 계약은 없다 |
| `other_investments` | `current`(allowlist) / 정규화 deferred | OpenDART `otrCprInvstmntSttus` 계열 타법인 출자 현황 문서 | parsed payload 보존까지만 current로 관찰된다. 본 문서 기준 정규화 사실·graph projection 계약은 없다 |
| `listing_products` | `current`(allowlist only) / 의미 상세 미확정 [INFERENCE] | 제품/상품 목록형 공시를 담기 위한 reserved bucket으로 보인다. 현 저장소에서는 enum 존재 외의 추가 lineage가 관찰되지 않는다 | downstream current 계약 없음 |
| `generic` | `current`(fallback bucket) | 특정 공시 family로 승격하지 못한 parsed disclosure를 담는 일반 버킷 | 정규화 사실·graph relation의 current 입력으로 쓰지 않는다 |

### 타입 구분 원칙

- **allowlist**는 “lake에 저장할 수 있다”는 뜻이지, 자동으로 정규화·graph화된다는 뜻이 아니다.
- **current normalized**는 현재 local lineage에서 parser shape와 downstream 의미가 함께 관찰된 타입만 가리킨다. 지금은 `supply_contract`, `business_segments` 두 종류다.
- `listing_products`처럼 enum만 보이고 parser lineage가 보이지 않는 타입은 의미를 단정하지 않는다. 이 문서에서는 `[INFERENCE]`로만 다룬다.

## 정규화 사실 타입 카탈로그

여기서의 “사실 타입”은 raw `parsed` JSON을 downstream이 직접 해석하지 않도록, 공시 문서의 핵심 의미를 재사용 가능한 contract로 승격한 것이다.

### 1. `SupplyContractFact`

- **grain**: 한 공시가 주장하는 **issuer ↔ counterparty 계약 사실 1건**
- **source `doc_type`**: `supply_contract`
- **답하는 질문**: “누가 누구와 어떤 공급/판매 계약을 체결했고, 계약 규모/비중/유효기간은 무엇인가?”

타입 수준 shape는 아래 의미 슬롯으로 충분하다.

| 의미 슬롯 | 설명 |
|---|---|
| `issuer_snapshot` | filing이 가리키는 발행사 snapshot (`corp_name`, `corp_code`, filing-level ticker 후보). canonical issuer 확정은 bridge가 맡는다 |
| `counterparty_identity` | 계약 상대방의 공개 이름 또는 비공개 상태. 공개되지 않으면 그래프 relation으로 승격하지 않을 수 있다 |
| `contract_object` | 무엇을 공급/판매하는 계약인지 나타내는 계약 대상 설명 |
| `value_signal` | 계약 규모를 나타내는 금액/매출비중 신호. 둘 중 하나만 있어도 사실이 성립할 수 있다 |
| `validity_window` | 계약 시작/종료 또는 report-date 기반 유효 구간 |
| `quality_flags` | `confidence`, 상대방 비공개 여부, late disclosure 여부처럼 downstream 해석 강도를 조정하는 품질 신호 |
| `source_evidence` | amount/ratio 원문 등 재근거용 텍스트 조각 |

이 타입은 숫자 fact이면서 관계 신호를 담는다. 관계 엣지 투영(구 D)은 [관계 그래프 draft](../../proposals/0002-relationship-graph.md)로 강등했고, 현재는 계약금액·매출비중 같은 **수치·노출 evidence(E 중요도)**로만 쓴다.

### 2. `BusinessSegmentFact`

- **grain**: 한 공시가 주장하는 **issuer · period · segment 구성 사실 1건**
- **source `doc_type`**: `business_segments`
- **답하는 질문**: “이 issuer의 어느 기간 매출이 어떤 부문/제품군에서 얼마나 발생했는가?”

타입 수준 shape는 아래 의미 슬롯으로 충분하다.

| 의미 슬롯 | 설명 |
|---|---|
| `issuer_snapshot` | filing이 가리키는 발행사 snapshot. canonical issuer 확정은 bridge가 맡는다 |
| `segment_identity` | `segment_name`으로 대표되는 사업부문/제품군 라벨 |
| `period_context` | 어떤 회계 기간의 구성인지 나타내는 기간 문맥 |
| `revenue_signal` | `revenue_share_pct` 및/또는 `revenue_krw`. 둘 다 없으면 사실이 약해진다 |
| `share_quality` | reported/rescaled/computed/unreliable처럼 비중 신뢰도를 나타내는 quality 신호 |
| `link_intent` | segment가 어떤 concept/family와 연결돼야 하는지에 대한 intent. verified exact match면 active relation으로, generic/ambiguous면 review 또는 draft로 남는다 |
| `source_evidence` | segment label, linker 판단 근거, period 문맥 등 재근거용 정보 |

이 타입은 “하나의 이벤트”보다 **issuer의 사업 구성/노출 구조**를 설명하는 사실이다. 현재는 사업부문 매출비중 **노출 evidence**로 쓰며, `produces` 관계 엣지 투영은 [관계 그래프 draft](../../proposals/0002-relationship-graph.md) 강등이다.

### 3. 두 사실 타입의 차이

| 축 | `SupplyContractFact` | `BusinessSegmentFact` |
|---|---|---|
| 주체 구조 | issuer ↔ counterparty의 **이항 관계** | issuer → segment/theme의 **구성 관계** |
| 핵심 수치 | 계약금액, 매출비중, 기간 | 매출비중, 매출액, 기간 |
| 관계 투영(강등) | `supplies` → draft | `produces` → draft |
| 해석 위험 | 상대방 비공개, issuer/counterparty 해소 실패 | generic segment, region/accounting row 혼입, share basis 불안정 |

## issuer-security bridge 개념

공시 원문은 filing 세계의 식별자(`corp_code`, filing-level `stock_code`)를 사용하고, HQ/graph는 증권 세계의 식별자(`market`, `ticker`, `kind_stock_code`)를 사용한다. `dart_corp_security_map`은 이 둘을 혼동 없이 잇는 **선행 bridge contract**다.

| 구분 | canonical anchor | 의미 |
|---|---|---|
| filing snapshot axis | `corp_code`, `corp_name`, filing-level `stock_code/ticker` | 원문이 실제로 무엇을 적었는지 보존하는 provenance 축 |
| issuer-security bridge axis | `corp_code -> (market, ticker, kind_stock_code)` | downstream mart, graph, evidence가 issuer를 다른 데이터 family와 같은 증권 축으로 읽게 하는 canonical 연결 |
| 사용 원칙 | raw provenance는 snapshot을 남기되, 관계·evidence의 issuer join은 bridge 축을 우선한다 | filing에 적힌 ticker 문자열만으로 issuer 동일성을 확정하지 않는다 |

이 개념은 [엔티티 마스터](entity-master.md)의 canonical 엔티티 브리지와 역할이 같다. 차이는 disclosure가 뉴스의 mention 해소가 아니라 **법인코드 기반 issuer-security crosswalk**를 먼저 요구한다는 점이다.

## 공시 관계 타입 (강등)

공시 사실을 관계 엣지(`supplies`/`produces` 등)로 투영하는 부분은 복잡성 감축으로 [관계 그래프 draft](../../proposals/0002-relationship-graph.md)로 강등했다. `SupplyContractFact`·`BusinessSegmentFact`는 이 문서에서 **수치·노출 evidence(계약금액·매출비중·기간)**로 유지되며, 이는 Explanation E(중요도)의 규모·무결성 근거다. relation allowlist(`produces`/`supplies`/`owns`/`substitute`/`complement`/`input_of`)와 projection 규칙은 draft가 소유한다.

## downstream event / evidence에서의 위치

공시 타입 카탈로그는 `event_type_id` taxonomy 자체를 소유하지 않는다. 다만 local lineage에서 관찰되는 **공시 사실의 event/evidence 역할**은 아래처럼 정리할 수 있다.

| source type | downstream 역할 | 관찰 근거 |
|---|---|---|
| `supply_contract` | disclosure assertion / event evidence의 직접 근거. 예시 lineage에서는 `event_type_id = COMPANY.CONTRACT.SIGNING`이 등장한다 | [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) §6 교차소스 정합(아래 절의 대표 사례 서술 참조) |
| `business_segments` | 직접적인 event taxonomy보다 exposure/composition evidence의 성격이 강하다. 현 저장소에서는 명시적 standalone `event_type_id` 예시가 관찰되지 않는다 | `produces` relation projection과 segment linker lineage |

즉 공시 계층의 안정된 책임은 **문서 타입 → 사실 타입 → relation/evidence 타입**까지다. 정규화 완료된 doc_type의 `event_type_id` 귀속은 아래 매핑 계약이 고정하고, doc_type 확장 시 매핑 행도 함께 등재한다.

### `doc_type` → `event_type_id` 매핑 계약

| doc_type | event_type_id | identity 매핑 (카탈로그 role) | payload 매핑 |
|---|---|---|---|
| `supply_contract` | `COMPANY.CONTRACT.SIGNING` | 공시자 `corp_code`→`SUPPLIER`(주체 anchor), `counterparty`→`CUSTOMER`, `object`→`CONTRACT_OBJECT` | `amount_krw`→`CONTRACT_VALUE`, `ratio_pct`→매출 대비 비중, 계약기간→`CONTRACT_DURATION`. `report_nm`의 `[기재정정]` 접두는 §7 correction marker. `counterparty_withheld`(비공개)는 identity 결측 — `EMIT_UNKNOWN_LINK_ONLY` 처리(추측 배정 금지) |
| `business_segments` | **이벤트 아님** — exposure/composition evidence 전용. event thread를 만들지 않으며 D(영향 경로)·E(중요도)의 근거로만 소비한다 | — | `BusinessSegmentFact`(부문 매출비중) |

뉴스와 공시가 같은 사건을 다룰 때의 정합은 이 카탈로그가 아니라 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) §6(교차소스 이벤트 정합)이 소유한다. 요약하면, 매칭되는 뉴스 이벤트가 이미 있으면 공시 사실은 새 이벤트를 만들지 않고 그 뉴스 이벤트의 thread에 **확인 증거(cross-source confirming evidence)**로 링크된다. 이때 시점·선후 권위는 최초 소스(대개 뉴스)가, 계약금액·매출비중·상대방 같은 정밀 사실의 권위는 공시가 가진다. `supply_contract`가 `COMPANY.CONTRACT.SIGNING`으로 분류되는 경우가 대표 사례다.

## 대안

| 대안 | 판단 |
|---|---|
| [Data Ingestion 디자인](../../baseline/data-ingestion.md) 하나에 타입 설명까지 유지 | 운영 흐름 문서가 다시 비대해지고, current/allowlist/deferred 구분이 약해진다 |
| 모든 `doc_type`을 즉시 정규화 사실로 승격 | `major_shareholders`, `other_investments`, `listing_products`의 local lineage가 아직 부족하다. enum 존재와 current normalization을 분리하는 편이 안전하다 |
| graph store allowlist 전체를 disclosure current relation처럼 문서화 | `owns` 등은 substrate capability일 뿐 current DART projection이 아니다. 본 문서는 current 출력과 allowlist를 구분한다 |

## 위험과 실패 처리

- **enum drift 위험**: 코드의 `DOCUMENT_TYPES`와 문서 카탈로그가 어긋날 수 있다. 새 타입 추가 시 이 문서의 current/allowlist/deferred 상태도 같이 갱신해야 한다.
- **issuer 축 혼선**: filing snapshot ticker를 canonical issuer로 오인하면 다른 family와의 join이 깨진다. relation/evidence 단계에서는 bridge 축을 우선한다.
- **상대방 비공개 위험**: `supply_contract`는 counterparty가 비공개면 관계 그래프가 약해진다. 이 경우 numeric evidence는 남기되 relation confidence를 낮춰야 한다.
- **generic segment 위험**: `business_segments`는 사업부문명이 너무 일반적이면 `produces`를 과신하기 쉽다. generic/ambiguous linker 결과는 active relation로 승격하지 않는다.
- **reserved type 의미 공백**: `listing_products`는 enum으로만 관찰된다. parser/contract lineage가 생기기 전까지는 `[INFERENCE]` 이상의 의미를 부여하지 않는다.

## Open questions

1. `listing_products`의 실제 upstream source와 intended semantic boundary는 무엇인가?
2. `major_shareholders`, `other_investments`를 다음 정규화 사실 후보로 볼지, 아니면 parsed lake 보존 타입으로만 남길지?
3. disclosure 전용 `event_type_id` catalog를 별도 문서로 만들 필요가 있는지, 아니면 facts→evidence bridge까지만 유지하고 canonical event 정합은 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) §6(교차소스 이벤트 정합)에 위임할지.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| 문서 | `docs/engineering/design/data-ingestion.md` | current normalization 범위, issuer bridge 의미 |
| 코드 | `src/alphamale/graph/storage/doc_store.py` | `dart_documents` allowlist `DOCUMENT_TYPES`, parsed lake grain |
| 코드 | `src/alphamale/filings/dart/fetch.py` | `major_shareholders`, `other_investments`의 OpenDART endpoint lineage |
| 코드 | `src/alphamale/filings/dart/supply.py` | `SupplyContractFact`의 의미 슬롯, `supplies` projection 규칙 |
| 코드 | `src/alphamale/filings/dart/segments.py` | `BusinessSegmentFact`의 의미 슬롯, `produces` projection 규칙 |
| 코드 | `src/alphamale/filings/dart/segment_linker.py` | segment linker의 `category`/`region`/`accounting`/`generic`/`empty` 의미 구분 |
| 코드 | `src/alphamale/graph/storage/graph_store.py` | graph relation allowlist `REL_TYPES` |
| 문서 | `docs/engineering/specs/data/entity-master.md` | canonical bridge 문서 스타일과 엔티티 축 설명 기준 |
| 문서 | `docs/engineering/current-architecture.md` | disclosure를 공시 원문·정규화 사실 경계로 보는 상위 아키텍처 |
