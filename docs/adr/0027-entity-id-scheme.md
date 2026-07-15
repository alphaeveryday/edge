# ADR-0027: 도메인 ID 체계 — 불투명 서로게이트, 외부 식별자는 속성

- 상태: 승인됨
- 날짜: 2026-07-15

## 맥락

Cloud Event Store 물리 스키마(V202607150001)는 `entity_id`·`actor_id`·`instrument_id`를 `TEXT`로만 정의하고 **포맷을 정하지 않았다.** [event-bundle-schema.md](../contracts/event-bundle-schema.md)도 "Cloud 발번 TEXT"라고 타입만 못박았을 뿐이다(UUIDv7 제안은 기각되고 물리 스키마의 TEXT 도메인 ID가 채택됐다). `market_code VARCHAR(30) NOT NULL`과 `instrument.ticker`도 마찬가지로 CHECK·문서가 없다.

지금 결정해야 하는 이유는 **엔터티 마스터 시딩(ALPHA-362)이 첫 값을 넣는 순간 그 포맷이 사실상 계약이 되기 때문**이다. `entity_id`는 스키마 전역에서 19개 FK가 참조한다. 한 번 적재된 뒤 포맷을 바꾸려면 참조 전부를 마이그레이션해야 하고, assertion·event는 point-in-time 재현 대상이라 소급 수정이 특히 비싸다. 형제 사례가 이미 있다 — `document_assertion.modality_code`는 어휘가 정의된 적 없는 채 `NOT NULL`이라 적재를 통째로 막았고 ALPHA-361로 제약을 되물러야 했다. 값보다 규약을 먼저 박는다.

## 결정

- **도메인 ID는 불투명 서로게이트다** — `<타입접두사>_<ULID>` 형식의 `TEXT`. 접두사는 `actor_`·`inst_`·`concept_`이며 서브타입을 눈으로 식별하게 한다. 예: `inst_01KXJB6W2EFQRP1D5TBRF0EBEK`.
  - `entity`의 서브타입 테이블은 같은 값을 쓴다 — 회사의 `entity_id` = 그 회사의 `actor_id`(FK가 `(actor_id, entity_type)` → `(entity_id, entity_type)`이므로 값이 같아야 한다).
  - ULID를 쓰는 이유는 시간 정렬 가능하고 `TEXT` 한 컬럼에 담기며 별도 생성기 인프라가 필요 없어서다. UUID **컬럼 타입**을 쓰자는 게 아니므로 event-bundle-schema.md의 "TEXT 도메인 ID" 채택과 충돌하지 않는다.
- **외부 식별자는 ID가 아니라 속성이다** — 도메인 ID에 외부 식별자를 인코딩하지 않는다. 현재 컬럼이 있는 건 `instrument.ticker`(+`uq(market_code, ticker)`)와 `company_profile.dart_corp_code`(UNIQUE)뿐이다. **ISIN·LEI·FIGI는 아직 컬럼이 없다** — 필요해지는 시점에 확장으로 추가하며, 그때도 PK가 아니라 유니크 속성으로 붙인다.
- **`market_code`는 MIC(ISO 10383)** — KRX 유가증권시장 `XKRX`, 코스닥 `XKOS`. 미국 확장 시 `XNAS`·`XNYS`. 거래소 어휘를 자체 정의하지 않는다.
- **도메인 ID는 소비자에게 불투명하다** — On-Prem·tenant-sync-api를 포함한 어떤 소비자도 ID를 파싱해 의미(티커·시장)를 얻어선 안 된다. 필요한 속성은 컬럼에서 읽는다.

## 대안

- **자연키 파생 ID**(`inst_KRX:005930`처럼 시장+티커를 ID에 인코딩) — 사람이 읽기 쉽고 조인 없이 종목을 알아본다. 그러나 증권 마스터 업계 관행이 이걸 명시적으로 배격한다: 티커는 회사명 변경 시 함께 바뀌고 죽은 티커는 다른 회사에 재사용된다. 티커가 바뀌면 서로게이트는 `instrument.ticker` 한 컬럼만 UPDATE하면 19개 FK가 그대로 살지만, 파생 ID는 **영구히 잘못된 ID**로 남거나(`005930`인데 실제론 다른 코드로 거래) FK 전부를 마이그레이션해야 한다. 가독성은 `entity.display_name`·`instrument.ticker`가 같은 행에 있어 조인 한 번으로 회복되므로, 되돌릴 수 없는 결합과 바꿀 값이 아니다.
- **ISIN을 PK로** — 표준이고 KRX가 이미 발급한다(`KR7005930003`). 그러나 ISIN은 **증권의 수명 동안만** 유효하고 그 배후 사업체의 식별자가 아니다. 합병·분할은 새 법인을 만들어 새 ISIN을 낳고, 상폐된 ISIN은 유예 후 재할당된다 — 이력 DB가 ISIN을 재활용해 실제 결제 오류를 낸 사례가 보고돼 있다. 속성으로 두는 게 맞다.
- **UUIDv4** — 불투명하다는 목적은 같지만 정렬 불가라 인덱스 지역성이 나쁘고 시드·로그를 눈으로 훑기 더 어렵다. ULID는 같은 불투명성에 시간 정렬을 얹는다.
- **FIGI 채택** — 재사용 없음·기업행위 불변을 표준에 못박은 유일한 식별자라 이론적으로 가장 옳다. 그러나 외부 발급 의존이 생기고(OpenFIGI 조회) KR 커버리지 검증이 선행돼야 한다. 내부 ID는 서로게이트로 두고 FIGI는 필요해질 때 속성으로 추가하면 되므로 이 결정과 배타적이지 않다.

## 결과

- 시딩·적재 코드는 ID를 **발번**하고, 조회는 자연키(`(market_code, ticker)`·`dart_corp_code`)로 한다. 즉 upsert는 "자연키로 찾아보고 없으면 새 ULID 발번"이 표준 절차가 된다.
- 사람이 DB를 훑을 때 ID만으로는 종목을 못 알아본다 — `entity`/`instrument`를 조인해야 한다. 의도된 비용이다.
- 티커 변경 **이력**은 보존되지 않는다. 현업 증권 마스터는 식별자 변경에 effective dating을 붙여 과거 시점의 표기를 복원하지만, 현재 스키마는 `instrument.ticker` 단일 값이라 UPDATE하면 옛 티커가 사라진다. MVP 단순화로 수용하되, 과거 시점 티커 조회가 필요해지면 확장-수축으로 이력 테이블을 추가한다.
- `market_code`에 MIC를 쓰면 레이크 파티션의 `market=KR|US`와 값이 다르다(`XKRX` vs `KR`). 레이크는 시장 **지역** 파티션이고 RDB는 **거래소** 식별자라 층이 다르므로 의도적이다 — 적재 코드가 매핑한다.
- 이 ADR은 `libs/schema` CODEOWNERS(`@jingi723 @choyoungseo20`) 게이트를 통과해야 한다 — ADR-0026이 진기-영서 인터페이스를 DB 스키마 하나로 확정했으므로 ID 체계는 양자 합의 대상이다.

## 참조

- [ADR-0005](0005-db-as-contract.md) db-as-contract · [ADR-0026](0026-ownership-boundary-db.md) 오너십 경계 · [ADR-0024](0024-scope-domestic-etf.md) 국내 ETF MVP
- [event-bundle-schema.md](../contracts/event-bundle-schema.md) — "Cloud 발번 TEXT" 도메인 ID
- ALPHA-362(시딩) · ALPHA-361(modality — 미정의 어휘가 적재를 막은 형제 사례)
- 외부: [Modern Security Master Architecture (Intrinio)](https://intrinio.com/blog/modern-security-master-architecture-unifying-ticker-cusip-isin-and-figi-data-at-scale) · [FIGI](https://en.wikipedia.org/wiki/Financial_Instrument_Global_Identifier) · [ISO 10383 MIC — XKRX](https://www.tradinghours.com/mic/s/xkrx) · [LEI (GLEIF)](https://www.gleif.org/en/about-lei/introducing-the-legal-entity-identifier)
