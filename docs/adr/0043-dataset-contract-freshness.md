# ADR-0043: Dataset Contract와 ETF freshness 상태 축

- 상태: 승인됨
- 날짜: 2026-07-31

## 맥락

ETF 수집 완전성은 `ops_expected_task`에 기대·수집·누락 수를 남기지만, 같은 수의 데이터가
**요구한 기준일의 데이터인지**는 판정하지 않는다. 따라서 완전성은 `VALID`여도 직전 거래일
스냅샷을 다시 받은 `STALE`일 수 있다. 반대로 일부 ETF가 누락된 데이터도 수신한 행의 기준일은
현재여서 `INCOMPLETE`이면서 `FRESH`일 수 있다.

현재 `CatalogEntry`는 실행 작업의 정체성, SFN 상태, 의존, 실행 제한과 로그 위치를 함께 가진다.
여기에 데이터셋 cadence, 기준일 규칙, 허용 지연, 담당자와 향후 발행 정책까지 직접 추가하면 같은
데이터셋을 생산·가공하는 작업마다 계약이 복제된다. 또한 과거 원장을 현재 카탈로그 값으로 다시
판정하면 계약 변경 전 결과의 의미가 바뀐다.

첫 freshness 수직 슬라이스는 장 마감 후 수집하는
`ETF_HOLDINGS_COLLECTION_KRX`의 `etf_holdings` EOD 스냅샷이다. 이 수집기는 거래일에는 당일,
비거래일에는 `OPS_KR_HOLIDAYS`를 이용한 직전 거래일을 `trdDd`로 요청한다. 현재 raw
`trd_dd`는 KRX 응답 필드가 아니라 수집기가 그 요청값을 행에 덧붙인 provenance이고, canonical
`as_of_date`도 이를 변환한 값이다. 둘은 요청한 날짜를 증명하지만 응답 내용의 실제 기준일을
독립적으로 증명하지 않는다.

[ADR-0030](0030-raw-phase-partial-failure.md)은 일부 raw 실패나 부분 입력이 뒤의 정제·feature·분석
실행을 막지 않는다고 정했다. freshness는 데이터 상태와 외부 발행 판단을 위한 별도 사실이며,
ADR-0030의 실행 원칙을 바꾸면 안 된다.

## 결정

### 계약의 위치와 식별

- 데이터셋 운영 계약은 `CatalogEntry`의 필드 묶음이 아니라 data-pipeline `ops` 도메인의
  **별도 typed registry**로 둔다. 새 서비스나 자유 형식 함수 레지스트리는 만들지 않는다.
- 계약은 안정적인 `contract_key`와 명시적인 버전을 가지며, 데이터셋 전달 경계 하나를 식별한다.
  `CatalogEntry`는 해당 `contract_key`만 참조한다. 실행 정체성·SFN 배선·의존은 Catalog,
  cadence·기준일 규칙·허용 lag·필수 여부·재시도 소유자·운영 담당과 runbook은 Contract가
  소유한다.
- 기존 `CatalogEntry.required`는 additive 전환 동안 호환 필드로 유지하되 Contract의 값을
  투영하며, 두 값이 다르면 테스트와 Planner가 fail-loud한다. 독립적으로 수정하는 두 번째
  SSOT로 두지 않는다.
- Planner가 기대 작업을 만들 때 적용한 계약 키·버전과 해석된 기대값을 원장에 snapshot한다.
  Monitor는 과거 작업을 현재 registry 값으로 소급 재판정하지 않는다.
- 논리 이름 `expected_as_of`의 저장 SSOT는 기존
  `ops_expected_task.expected_as_of_date`다. 새 expected-as-of 컬럼을 병행 추가하지 않는다.
  Contract가 연결된 작업부터 Planner가 슬롯 날짜 대신 계약 규칙으로 해석한 날짜를 이 컬럼에
  기록한다. 기존 행은 당시 기록을 유지하고 소급 수정하지 않는다.
- 첫 계약은 `ETF_HOLDINGS_COLLECTION_KRX` 전달 경계를 대상으로 하며 다음 정책 코드를 가진다.
  cadence는 `MARKET_EVENT`, timezone은 `Asia/Seoul`, expected-as-of 규칙은
  `LATEST_KR_TRADING_DAY`, 허용 as-of lag는 0 거래일, retry owner는 `SFN`이다.

### 시간 값의 의미와 타입

네 값은 다음 의미로만 사용한다.

| 값 | 의미 | 첫 수직 슬라이스 타입 |
|---|---|---|
| `expected_as_of` | 이 실행 슬롯이 받아야 하는 데이터의 업무 기준일. 기존 `expected_as_of_date`에 저장하며 실행 시각이나 수집 시각이 아니다. | `DATE` |
| `actual_as_of` | 실제 수신 데이터가 나타내는 업무 기준일. 요청값과 독립된 source evidence로 검증될 때만 저장한다. | `DATE` |
| `collected_at` | 해당 task 범위의 immutable 수집 산출물과 로그가 저장되어 관측 가능해진 시각. 벤더 기준일이 아니다. | `TIMESTAMPTZ` |
| `observed_at` | Monitor가 계약과 증거를 비교해 freshness를 평가한 시각. 재평가하면 바뀔 수 있다. | `TIMESTAMPTZ` |

- `expected_as_of`는 실제 시작 시각이나 `now()`가 아니라 Planner의 **예정 슬롯**을
  `Asia/Seoul`로 해석해 계산한다. 이 계약에서는 그 로컬 날짜 이하의 최근 한국 거래일이다.
  수동·백필 실행도 대상 슬롯/런의 값을 명시적으로 이어받고 현재 날짜로 다시 만들지 않는다.
- `actual_as_of`는 응답 메타데이터처럼 요청값과 독립된 source evidence가 하나의 유효한
  업무 기준일을 증명할 때만 설정한다. 현재 adapter가 주입한 `trd_dd`와 여기서 파생한 canonical
  `as_of_date`는 `requested_as_of` provenance이지 이 증거가 아니다. KRX 응답에서 신뢰할
  실제 기준일을 확보하지 못하면 null로 두며, 요청일이나 파일 파티션 날짜로 추정하지 않는다.
- source evidence가 행 단위라면 수신한 행들이 하나의 기준일에 합의해야 한다. 값이 없거나
  파싱할 수 없거나 행마다 다르면 날짜를 설정하지 않는다.
- EOD 기준일에 임의 시각을 붙여 `TIMESTAMPTZ`로 만들지 않는다. 장중 데이터셋은 후속 계약에서
  timestamp grain과 cutoff 규칙을 별도로 정의해야 하며, 이 ADR의 `DATE` 컬럼에 넣지 않는다.
- `collected_at`과 `observed_at`은 UTC `TIMESTAMPTZ`로 저장하고 표시에만 계약 timezone을 쓴다.
  기존 행의 값이 없으면 null로 두며 계획 시각, attempt 종료 시각 또는 `0`으로 대체하지 않는다.

예를 들어 2026-07-31(금) 슬롯은 `expected_as_of=2026-07-31`이다. 2026-08-01(토)에 같은 레인이
실행되면 기대값과 KRX 요청값은 모두 최근 거래일인 2026-07-31이다. 그러나 adapter가 행에 붙인
`trd_dd=20260731`만 있고 응답 자체의 기준일 증거가 없다면 `actual_as_of`는 null이고 freshness는
`UNKNOWN`이다. 독립 증거가 2026-07-31을 확인해야 `FRESH`가 된다. 산출물이
`2026-08-01T07:10:00Z`에 저장되고 Monitor가 `07:12:00Z`에 판정했다면 그 두 시각이 각각
`collected_at`, `observed_at`이며 어느 것도 실제 기준일 증거를 대신하지 않는다.

### freshness 판정

`freshness_status`는 `UNKNOWN`, `FRESH`, `STALE`만 가지며 `data_status`와 독립적으로 저장한다.
판정은 Contract와 원장 증거를 읽는 결정론적 규칙이 수행하고 UI나 LLM은 재계산하지 않는다.
계약이 연결된 `DUE` 작업은 `UNKNOWN`에서 시작한다. 계약이 아직 연결되지 않았거나
`SKIPPED`여서 freshness 축이 적용되지 않는 작업의 저장값은 null이며, null은 `UNKNOWN`이나
`FRESH`의 다른 표기가 아니라 `NOT_APPLICABLE` 의미다.

첫 ETF holdings 계약의 판정표는 다음과 같다.

| 조건 | `freshness_status` | reason |
|---|---|---|
| 평가 전이거나 기대/실제 기준일 증거가 없거나 요청값과 독립적으로 검증되지 않음 | `UNKNOWN` | `EVIDENCE_MISSING` 또는 `ACTUAL_AS_OF_UNVERIFIED` |
| 기준일 evidence 형식이 잘못됨 | `UNKNOWN` | `AS_OF_INVALID` |
| 수신 행에 둘 이상의 기준일이 섞임 | `UNKNOWN` | `ACTUAL_AS_OF_MIXED` |
| `actual_as_of`가 `expected_as_of`보다 미래임 | `UNKNOWN` | `ACTUAL_AS_OF_AFTER_EXPECTED` |
| 두 기준일이 같음(허용 lag 0 거래일 충족) | `FRESH` | `AS_OF_MATCH` |
| `actual_as_of`가 `expected_as_of`보다 이전임 | `STALE` | `ACTUAL_AS_OF_BEFORE_EXPECTED` |

두 상태 축은 다음처럼 조합되며 어느 한쪽이 다른 쪽을 덮어쓰지 않는다.

| `data_status` | `freshness_status` | 의미 |
|---|---|---|
| `VALID` | `FRESH` | 기대한 수와 기준일을 모두 충족 |
| `VALID` | `STALE` | 수는 완전하지만 오래된 스냅샷 |
| `INCOMPLETE` | `FRESH` | 일부 대상이 누락됐지만 수신 데이터의 기준일은 현재 |
| `INCOMPLETE` | `STALE` | 일부 대상이 누락됐고 수신 데이터도 오래됨 |
| 모든 `data_status` | `UNKNOWN` | completeness 사실은 보존하고 freshness만 미확정 |

`VALID_EMPTY`와 `INVALID`도 같은 독립 규칙을 적용한다. freshness 증거가 있으면 각각
`FRESH`/`STALE`과 조합될 수 있고, 없으면 `UNKNOWN`이다.

도착이 deadline보다 늦어도 기준일이 맞으면 freshness는 `FRESH`다. 지각 실행은 기존
plan/outcome/issue 축에서 드러내며 데이터의 나이와 섞지 않는다. 반대로 실행이 성공하고
완전성이 `VALID`여도 기준일이 오래됐으면 `STALE`다. freshness가 `UNKNOWN`이어도 기존
`data_status`를 `UNKNOWN`, `VALID` 또는 `INCOMPLETE`로 덮어쓰지 않는다.

### 실행, 재시도와 향후 발행

- SFN이 bounded 실행 재시도의 단일 소유자다. Reconciler/Dataset Monitor는 관측·판정·이슈
  전이·evidence 저장만 하고 ECS task나 SFN 실행을 시작하지 않는다.
- `freshness_status`는 정제·feature·분석 실행 게이트가 아니다. `STALE` 또는 `UNKNOWN`이어도
  ADR-0030에 따라 실행은 계속되고 런의 실행 결과와 freshness가 각각 남는다.
- 외부 발행은 후속 Publish Gate의 `publication_decision` 축에서만 제어한다. Gate가 도입되기
  전에는 현재 발행 동작을 이 ADR로 바꾸지 않는다.
- 향후 Gate에서 필수 데이터셋의 freshness가 `UNKNOWN`이면 기본 결정은 `PENDING`이다.
  조용히 `ALLOW`하거나 `FRESH`로 간주하지 않는다. `STALE`의 `REVIEW`/`BLOCK` 정책과 영향
  범위는 Impact Resolver·Publish Gate 티켓에서 계약별로 확정한다.

### 배포 단위와 순서

이 ADR 이후 구현은 한 PR에 합치지 않고 다음 순서를 지킨다.

1. KRX 응답에서 요청값과 독립된 `actual_as_of` evidence를 확보할 수 있는지 검증
2. additive migration과 ETF freshness evidence writer
3. Reconciler의 `STALE` 판정과 evidence/상태 전이
4. 운영 조회 API
5. UI 표시
6. Impact Resolver와 Publish Gate

독립 evidence를 확보하지 못하면 writer는 `actual_as_of=null`,
`freshness_status=UNKNOWN`, reason=`ACTUAL_AS_OF_UNVERIFIED`를 저장하며 `FRESH`/`STALE`를
만들지 않는다. writer가 먼저 nullable 필드를 채우고 observe-only로 검증한 뒤 reader, UI
순으로 배포한다. 이 ADR에는 스키마, 파이프라인, API, UI 변경을 포함하지 않는다.

## 대안

**대안 1 — 운영 계약을 모두 `CatalogEntry`에 추가한다.** 작업 조회는 단순하지만 데이터셋
전달 계약이 실행 작업 수만큼 복제되고, 담당·runbook·발행 정책까지 실행 카탈로그에 쌓인다.
Catalog는 `contract_key`만 참조하고 계약의 버전과 해석 결과를 원장에 남기는 편이 책임과
과거 판정의 재현성을 지킨다.

**대안 2 — 모든 as-of를 `TIMESTAMPTZ`로 통일한다.** ETF holdings의 업무 기준은 날짜이며
KRX가 기준 시각을 제공하지 않는다. 자정이나 장 마감 시각을 임의로 붙이면 제공되지 않은
정밀도를 만들므로 EOD는 `DATE`, 실제 사건 시각은 `TIMESTAMPTZ`로 분리한다.

**대안 3 — completeness와 freshness를 하나의 상태로 합친다.** `VALID_STALE`,
`INCOMPLETE_FRESH` 같은 조합 상태가 늘고 한 축의 변화가 다른 사실을 덮는다. 두 축을 독립
보존하고 소비자가 목적에 맞게 함께 읽도록 한다.

**대안 4 — Reconciler가 stale 데이터를 다시 수집한다.** SFN retry와 경합해 중복 실행과
서로 다른 재시도 이력이 생긴다. Reconciler는 사실 판정과 에스컬레이션만 맡긴다.

**대안 5 — freshness `UNKNOWN`을 즉시 발행 `ALLOW` 또는 `BLOCK`으로 바꾼다.** `ALLOW`는
근거 없는 정상화이고 `BLOCK`은 ADR-0030의 부분 입력 실행과 아직 없는 영향 계산을 사실상
발행 단계에서 전역 차단으로 바꾼다. 불확실성을 보존하는 `PENDING`이 기본값이다.

## 결과

- 실행 성공, 완전성, freshness와 향후 발행 결정을 각각 감사할 수 있다.
- 비거래일 KRX 수집은 직전 거래일을 기존 `expected_as_of_date`에 기록하므로 슬롯 날짜와
  계약 날짜가 갈리지 않는다.
- 요청 기준일을 실제 기준일로 재사용하지 않으므로 오래된 응답에 거짓 `FRESH`를 주지 않는다.
  검증 가능한 KRX evidence가 없을 때 `UNKNOWN`이 늘어나는 것은 의도한 fail-loud 결과다.
- 계약 변경 뒤에도 원장에 snapshot된 버전과 기대값으로 과거 판정을 설명할 수 있다.
- typed registry의 버전 관리와 Catalog 참조 무결성을 검증하는 테스트가 후속 구현의 의무가 된다.
- 첫 범위는 KRX ETF holdings EOD뿐이다. NAV·ETF profile·장중 데이터, 단계별 lineage,
  Impact Resolver, Publish Gate, API와 UI는 각각 후속 Jira/PR로 남는다.
