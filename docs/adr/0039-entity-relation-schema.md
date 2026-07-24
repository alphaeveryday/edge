# ADR-0039: 엔티티 관계 스키마 — 기존 3서브타입 유지, 관계·별칭·멘션 3테이블 추가

- 상태: 승인됨
- 날짜: 2026-07-24

## 맥락

온톨로지 thread 계약(`news_thread_contract_v0_1.yaml`)은 타입별 `relation`(owns·supplies·member_of 등)을 선언하지만 DB 에 관계 저장처가 없다. 실존 관계는 `equity_profile.issuer_actor_id` 와 `etf_holding_snapshot` 두 개뿐이다. 또한 온톨로지 `entity_kinds` 7종 중 DB 가 실제로 만드는 것은 actor·instrument 뿐이라, AUTHORITY·PRODUCT·INDEX 류 identity 역할을 가진 타입은 해소 불가 → 구조적으로 UNKNOWN thread 로 빠진다. 해소기는 완전일치 3축뿐이고(별칭 없음), 미해소 표면 문자열은 DB 에 남지 않아(스킵+계측 원칙) 재해소·중복 집계·마스터 큐레이션이 불가능하다. ALPHA-509(엔티티 관계 트리 정의)가 이 층을 확정해야 후속 구현(별칭 해소·관계 projection)이 진행된다.

## 결정

1. **엔티티 kind 7종은 기존 `entity` 3서브타입(+`market_series`) 어휘로 사상한다** — 새 서브타입 테이블을 만들지 않는다. 회사의 캐노니컬 ID 는 instrument 가 아니라 **actor** 다. 사상표는 [contracts/entity-relations.md](../contracts/entity-relations.md) §1.
2. **`entity_relation`** — 단방향(subject→object)+유효기간(valid_from/to)+소스 우선순위(DECLARED > EVENT_DERIVED > PROVIDER). 멱등키는 (thread×relation)·(fact×relation) 부분 유니크. 병합은 저장이 아니라 조회 계층 책임.
3. **`entity_mention`** — 미해소 표면 문자열의 1급 보존처. `RESOLVED ↔ entity_id NOT NULL` CHECK 로 placeholder 마스터 생성을 차단하고, UNKNOWN thread 재평가와 동형의 승격 경로를 연다. 확정 링크 소유권(`assertion_argument`·`event_argument`, NOT NULL FK)은 불변.
4. **`entity_alias`** — 해소 4축째. 동명 충돌은 `is_ambiguous` 마킹 후 결정적 매칭에서 제외.
5. **어휘 위치**: `relation_code`·`kind_hint` 는 온톨로지 소관 어휘라 SQL CHECK 로 발명하지 않는다(계약 문서가 정의, 적재 코드가 검증). `resolution_status`·`source_kind`·`alias_type` 은 edge 소유 구조 어휘라 CHECK 로 못박는다. `edge_ontology` 리소스는 현지 수정하지 않는다(통째 교체 규약, ALPHA-539) — 관계 어휘 정본은 당분간 edge 계약 문서이며, 실험실 확정본이 나오면 리소스 개정에 정합시킨다.
6. **파생/링크 테이블 PK 는 BIGINT IDENTITY** — ADR-0027 의 ULID 도메인 ID 는 마스터 객체 전용을 유지한다.

## 대안

- **그래프 DB(또는 별도 그래프 스토어) 도입** — 관계 질의는 유리하나 저장소·운영 축이 하나 늘고, 현 규모(관계 어휘 9종, 조회는 subject/object 인덱스로 충분)에서 과설계다. RDB 관계 테이블로 시작하고 질의 복잡도가 실증되면 재검토.
- **kind 별 서브타입 테이블 증설**(authority·location·index_master 등) — 테이블 7개·FK 폭증 대비 각 테이블의 고유 속성이 아직 없다. `actor_type`·`concept_type` 어휘로 충분하며, 고유 속성이 생기는 시점에 profile 테이블(예: `company_profile` 전례)로 확장한다.
- **관계를 양방향 2행으로 저장** — 조회는 쉬워지나 정합 유지 비용(2행 동기화)이 생기고, 단방향+인덱스 2개(subject·object)로 같은 질의가 된다.
- **미해소 문자열을 `entity` 에 PROVISIONAL 행으로 적재** — 마스터 오염(21개 FK 가 참조하는 테이블에 미검증 행)이 정확히 modality_code 사례가 경고한 되돌리기 비싼 실수다. 멘션 테이블로 격리한다.
- **`relation_code` 를 CHECK 로 고정** — 어휘가 온톨로지(실험실) 소관이라 리소스 개정마다 마이그레이션이 필요해진다. V202607150003(모달리티 제약 완화)의 교훈대로 구조만 스키마가 소유한다.

## 결과

- 후속 티켓 3건이 이 스키마 위에서 독립 진행 가능: 별칭 해소(entity_resolution 4축), 멘션 적재+재해소 배치, 관계 projection+병합 뷰.
- AUTHORITY·PRODUCT 류 kind 의 마스터 등재가 시작되면 해당 타입의 UNKNOWN thread 비율이 내려간다 — `unknown_reason` 계측이 개선 지표.
- 관계·멘션은 재현 가능한 파생물이라 마스터(ADR-0027)와 달리 재적재로 복구 가능 — 소급 수정 비용이 낮은 층에 두었다.
- `libs/schema` 변경이므로 CODEOWNERS(@jingi723 @choyoungseo20) 공동 승인 게이트를 지난다(ADR-0026).
- 이미 적용된 마이그레이션은 수정하지 않는다 — 이 변경은 신규 `V202607241600` 추가로만 이뤄졌다(확장-수축).

## 참조

- [ADR-0027](0027-entity-id-scheme.md) 도메인 ID 체계 · [ADR-0026](0026-ownership-boundary-db.md) 오너십 경계 · [ADR-0005](0005-db-as-contract.md) db-as-contract
- [contracts/entity-relations.md](../contracts/entity-relations.md) — 관계 어휘·병합 규칙 SSOT
- `edge_ontology` 리소스: `news_thread_contract_v0_1.yaml`(relation 선언) · `entity_mapping_contract_v0_1.yaml`(kind 7종·future_entity_backlog)
- ALPHA-509(이 결정) · ALPHA-539(리소스 통째 교체 규약) · ALPHA-361(미정의 어휘 CHECK 의 교훈)
