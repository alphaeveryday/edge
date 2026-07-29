# ADR-0039: Screening 판정의 DDD 전환은 사건 기반 — 첫 테넌트 기준 연결이 방아쇠

- 상태: 승인됨
- 날짜: 2026-07-24 (채택 2026-07-29 — 방아쇠는 PR #326 으로 이미 발동, 아래 "발동 기록")

## 맥락
`screening-worker` 의 판정 코어(`BundleScreener`)를 언제 DDD 택틱(안티커럽션 계층·순수 도메인 코어·공유 커널)으로 전환할지에 대한 반복 질문이 있었다. "모듈을 DDD 로 바꾸자"는 프레이밍으로 접근하면 전면 전환이냐 아니냐의 이분법이 되는데, 이는 이 코드베이스의 두 전제와 충돌한다.

- **스키마 SSOT 는 Flyway 다(ADR-0005·0038).** 앱은 `ddl-auto=validate` 로 스키마를 읽기 전용으로 따르고, 상태 전이·게시 grain 선점은 native SQL 로 쓴다. 애그리거트가 자기 영속성을 소유하는 클래식 DDD 는 이 경계와 정면충돌한다.
- **얇은 레이어드가 관례다.** 온프렘 모듈은 controller/service/repository 를 형식만 유지한 최소 두께이며, 오버엔지니어링을 명시적으로 배제해 왔다.

동시에 screening 은 이 레포에서 **도메인 규칙이 실제로 쌓일 유일한 모듈**이다. 현재는 walking skeleton 국면이라 판정 로직이 delivery_type 3분기(NEW/CORRECTION/INVALIDATION — ADR-0014·0015) **뿐**이고, 이는 도메인 규칙이 아니라 전달 프로토콜 분기다. `BundleScreener` 는 이 분기 + JSON 계약 파싱 + repository 호출이 한 클래스에 섞여 있지만, 로직 총량이 얇아(≈150줄) 지금은 트랜잭션 스크립트가 정직하다.

문제는 "언제 이 정직함이 깨지는가"를 취향이 아니라 관측 가능한 기준으로 고정하는 것이다. 미리 지으면(선제적 DDD) 정작 규칙이 도착했을 때 예측한 경계가 틀려 두 번 일하고, 너무 늦으면 규칙이 `if` 로 흩어져 추적 불능이 된다.

## 결정
**Screening 판정의 DDD 전환은 달력이 아니라 사건으로 방아쇠를 당긴다.**

1. **방아쇠(전환 시점).** 첫 번째 **테넌트 설정 기준**(`tenant-console-api` 의 BannedWord·Disclaimer·MarketScope·StockScope·Criteria)이 screening 판정 결정에 실제로 반영되는 커밋. 이 시점에 판정이 "delivery_type 분기"에서 "내용을 기준에 대어 AUTO_PUBLISHED / REVIEW_REQUIRED / BLOCKED 를 정하는 결정"으로 성격이 바뀐다 — 규칙이 얽히고(∧ 조건 다수), 개념이 모듈 경계를 넘고(screening·review 공유), 설정값으로 자주 바뀌며, I/O 없이 테스트해야 할 이유가 동시에 생긴다.

2. **전환 시 카빙 형태(그 커밋에서 할 일).**
   - `delivery/` — 안티커럽션 계층. 번들 JSON 을 typed VO(`DeliveryBundle`·`DeliveryEntry`)로 1회 파싱·계약 검증. 흩어진 `JsonNode.path(...).asString(null)` + `IllegalStateException` 을 대체한다.
   - `policy/` — 순수 결정 코어. `DeliveryEntry`(+ 기준) → `ScreeningDecision`. I/O 를 모른다. 실 스크리닝 규칙이 여기 쌓인다.
   - **공유 `ledger` 커널** *(원안 — 채택 시 유예로 대체됨, 아래 "발동 기록" 참조)* — `AnalysisItemStatus`·`PublicationStatus` 와 전이 규칙은 screening·`tenant-console-api` 두 컨텍스트가 공유하므로 `libs/jvm-common`(또는 공유 커널)으로 올린다. screening 안에만 두면 review 쪽과 드리프트한다.
   - `BundleScreener` 는 parse → decide → apply 오케스트레이터로 얇아진다.

3. **불변식은 SQL 에 남긴다(전환해도 옮기지 않는다).** terminal 가드(`status NOT IN ('CORRECTED','INVALIDATED')`)와 게시 grain 유일성(`NOT EXISTS ... status='PUBLISHED'`)은 동시 writer(screening + tenant-console) 환경에서 DB 가 원자적으로 강제해야 정확하다. 도메인 코드는 *의도·결정*을 표현하고, SQL 은 *불변식*을 강제한다 — 같은 가드를 도메인에 복제하면 진실의 원천이 둘이 되어 스키마 SSOT 와 싸운다.

4. **방아쇠 전에는 위생만 유지한다.** 새 delivery_type 분기나 `if` 하나가 붙어도 `BundleScreener` 안에 둔다. 단 새 로직을 넣을 때 "무엇을 할지 정하는 줄(결정)"과 "실제로 쓰는 줄(I/O)"이 뒤엉키지 않게만 한다 — 이는 DDD 가 아니라 위생이며, 방아쇠가 왔을 때 `policy` 추출을 리라이트가 아닌 이동으로 만든다.

5. **"너무 늦음" 반대 신호(하나라도 보이면 즉시 카빙).** 방아쇠를 놓쳤다는 뜻이다.
   - `BundleScreener`(또는 `screenNew` 계열)가 ≈250줄 초과, 또는 판정 `if/switch` 분기 5개 이상.
   - 판정 규칙 하나를 고쳤는데 무관한 delivery_type 테스트가 깨짐(관심사 누수).
   - "이 규칙이 지금 어디서 적용되나"를 코드에서 한눈에 못 찾음.

## 대안
- **지금 선제적으로 DDD 전환** — 아직 판정 로직이 프로토콜 분기뿐이라 도메인이랄 게 없다. 규칙이 도착하기 전에 경계를 그으면 추측한 애그리거트가 틀려 두 번 일하고, 얇은 레이어드 관례와 스키마 SSOT 전제를 근거 없이 되돌린다. 배제.
- **영구 트랜잭션 스크립트(DDD 안 함)** — 테넌트 기준이 붙기 시작하면 규칙이 얽히고 두 모듈이 상태 언어를 공유해야 하므로, 언젠가 `if` 지옥·드리프트로 귀결된다. 방아쇠를 정의하는 이 ADR 의 목적과 반대라 배제.
- **모듈 전체 DDD(엔티티가 영속성 소유·도메인 이벤트 버스·리포지토리 도메인 추상화)** — `ddl-auto=validate`·native 쓰기 전제와 충돌하고, 단일 DB·단일 프로세스 워커에 순수 비용이다. `delivery`+`policy` 카빙과 SQL 가드 유지라는 최소 형태만 채택.
- **intake·sync-agent 등에도 같은 패턴 확산** — 본질이 배관(I/O·상태전이)이라 도메인이 없다. screening 판정에 한정하고 확산하지 않는다.

## 결과
- 전환 판단이 취향 논쟁에서 **관측 가능한 사건 게이트**로 바뀐다 — "테넌트 기준 첫 연결 PR"이라는 단일 방아쇠와 3개의 반대 신호로, 리뷰어가 시점을 객관적으로 합의할 수 있다.
- `ledger` 상태·전이를 공유 커널로 올리는 작업은 screening 단독이 아니라 `tenant-console-api` 와 함께 조율해야 한다(공유 writer). 이 조율 부담은 그 승격을 실행하는 티켓이 안는다 — 승격 시점은 아래 "발동 기록"의 유예 결정(재방아쇠 2종)이 정한다(원안은 전환 티켓 동반이었으나 발동 커밋 #326 이 이행하지 않아 채택 시점에 유예로 대체).
- 이 ADR 은 시점 기준만 고정한다. 실제 카빙의 상세 시그니처(`ScreeningDecision`·`DeliveryEntry` 형상)는 발동 커밋 #326 에서 확정됐다(아래 "발동 기록") — 미확정으로 남는 것은 공유 커널 위치뿐이며, 재방아쇠 시점의 티켓이 이 결정을 근거로 확정한다.

## 발동 기록 (2026-07-29 채택 시점 정합)

초안(2026-07-24) 작성 후 채택 전에 방아쇠가 실제로 당겨졌다 — 이 절은 그 사실 기록이다(결정 본문은 작성 시점 그대로 둔다).

- **방아쇠 발동**: PR #326(2026-07-27, "정책 평가기 — 금칙어·임계값 상태 분기") — 테넌트 정책 기준(금칙어 BLOCK/REVIEW·자동 제공 스위치·`min_source_count`)이 screening 판정에 처음 반영된 커밋으로, 결정 1의 정의에 정확히 부합한다.
- **카빙 이행(공유 `ledger` 커널 제외 — 아래 잔여 항목)**: 같은 작업이 결정 2의 나머지 형태를 이행했다 — `delivery/`(`DeliveryBundleParser`·`DeliveryEntry`, 안티커럽션 계층)·`policy/`(`PolicyEvaluator`·`ScreeningDecision`·`ActivePolicy`·`PolicyRule`, 순수 결정 코어)·`BundleScreener` 는 parse→decide→apply 오케스트레이터. 코드 Javadoc 4곳이 이 ADR 을 "ADR-0039 §2"로 인용한다. 결정 3(불변식은 SQL)도 유지됐다 — terminal 가드·게시 grain 유일성은 native SQL 그대로.
- **잔여 — ledger 커널 유예(이 채택의 유일한 결정 변경)**: 결정 2 원안은 공유 `ledger` 커널 승격을 전환 커밋에서 함께 하도록 요구했으나, 발동 커밋(#326)은 이를 이행하지 않았다. 채택 시점 판단으로 소급 강제하지 않고 **별도 방아쇠로 유예**한다 — 근거: 지금 승격하면 `tenant-console-api` 와의 조율(공유 writer)이 실증된 필요 없이 선제 작업이 된다(초안이 배제한 선제적 DDD 와 같은 오류). 재방아쇠(관측 가능): ① 상태·전이 규칙 변경 하나가 screening·console **두 모듈의 동시 수정**을 요구하는 첫 PR, 또는 ② 두 모듈의 상태 문자열 리터럴 불일치가 리뷰/버그로 실증되는 순간 — 그때 승격을 티켓화한다(결정 5의 반대 신호는 `BundleScreener` 비대화 감시용으로 별개다).
- 상시 의무는 결정 4(결정/I-O 분리 위생) 그대로 유효하다.
