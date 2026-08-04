# ADR-0045: 실시간 스냅샷 게시 전환 — day-grain 게이트 폐지, 승계와 3축 무효화

- 상태: 제안됨
- 날짜: 2026-08-01
- 관련: [ADR-0044](0044-correction-abolition.md)가 발번 정책 소관으로 이연한 결정을 채운다.
  [ADR-0043](0043-dataset-contract-freshness.md)이 후속 계약으로 미룬 장중 timestamp grain과 연결된다.

## 맥락

제품 목표는 하루 1회 제공이 아니라 **최대한 실시간에 가까운 설명 제공**이다(사용자 확정
2026-08-01). 이 목표 아래에서 설명 하나의 정체는 "그날의 결론"이 아니라 **`explanation_as_of`
시점의 스냅샷**이고, 장중에 새 스냅샷이 이전 스냅샷 위에 연속으로 쌓인다.

현행 구현은 이와 충돌한다. analysis-engine 발번 게이트는 같은 (종목, 거래일)에 PUBLISHED가
이미 있으면 새 결과를 DRAFT로 보류한다(day-grain 게이트 — `eventstore.py`의 `EXISTS
PUBLISHED` CASE 분기). 그날 첫 결과만 나가고 재실행분은 묶이는 이 구조는 하루-1회 전제의
산물이다. [ADR-0044](0044-correction-abolition.md)는 CORRECTION을 폐지하면서 "무효화 이후
같은 종목에 새 설명(NEW)을 발번할 수 있는지는 발번 정책 소관"으로 명시적으로 이연했다 —
이 ADR이 그 발번 정책을 결정한다.

전송 채널의 메커니즘(cursor 기반 delta·폴링 주기·`limit` 파라미터)은 그대로 쓸 수 있다.
다만 [sync-protocol.md](../contracts/sync-protocol.md)의 규모 가정(평시 테넌트당 일 수십
건, 피크 1,000건)은 하루 1회 게시의 산물이므로, 실시간 게시 빈도 × 종목 수로 재산정해
폴링 주기·limit 상한을 함께 갱신해야 한다(후속 ④). 온프렘의 게시·표시 경로에도
day-grain 전제가 남아 있어(결과 절) cloud 게이트와 함께 걷어내야 한다.

## 결정

**게시 모델을 append-only 스냅샷 스트림으로 전환한다. 낡음은 승계(새 NEW)가 해소하고
승계가 끊긴 낡음은 만료 무효화(결정 2-(c))가 상한하며, 오류는 무효화(INVALIDATION)가
해소한다.**

1. **day-grain 게이트(DRAFT 보류)를 폐지한다.** 엔진 산출물은 보류 없이 즉시 PUBLISHED로
   게시하고 NEW를 발번한다. 같은 (종목, 거래일)의 게시 횟수 제한은 없다 — 스냅샷은
   `explanation_as_of`로 구분되는 별개 게시다.
2. **무효화 트리거는 세 축이다.** (a) **as_of 시점 오류** — 그 스냅샷의 기준 시점에서도
   틀렸던 것: ① 전제 데이터 소급 정정(가격·수익률 정정, 인용 뉴스 정정·철회) ② as_of 시점
   방향 모순(설명 방향 vs 원장 인과 수익률) ③ 근거 붕괴(lineage 원문 철회). (b) **노출
   정합 붕괴** — 노출될 수 있는 설명이 다루는 방향("왜 올랐나")과 현재 시세 방향(하락 중)이
   불일치하면 무효화한다. 스냅샷 사이 공백 동안 시세와 반대 방향의 설명이 걸려 있지 않게
   하는 가드로, "오류 발견 시 고치지 않고 내린다"는 ADR-0044 원칙과 같은 결이다. 이후
   시점의 새 정보 자체는 무효화 사유가 아니다 — 그것은 다음 스냅샷(NEW)의 소재다.
   (c) **만료** — 노출될 수 있는 스냅샷이 최대 연령을 초과하면 검증 시스템이 만료
   사유로 무효화한다. 이 상한은 절대 보장이 아니라 **목표 상한**이다 — 실제 노출
   시간은 최대 연령에 검증 주기·지연과 INVALIDATION의 테넌트 전파 지연(온프렘 폴링
   1~5분 + 소비·상태 전이)이 더해진 값이고, 검증 실행이 멈추면 표시 경로에 연령
   게이트가 없어 상한도 멈춘다. 임계값은 이 전 구간 지연을 포함한 실효 노출 시간
   기준으로 검증 실행 주기·지연 SLO와 함께 후속 ⑦에서 정량화한다. 연령 상한은 표시
   규칙이 아니라 이 무효화 축으로 강제한다 — 표시 규칙(결정 3)은 순수하게 유지된다.
3. **표시 규칙 "유효 최신 승리"를 계약으로 승격한다.** 종목별 노출 대상은 무효화되지 않은
   스냅샷 중 `explanation_as_of` 최신 1건이다.
   [publication-api.md](../contracts/publication-api.md)의 기존 "가장 최근 게시가 이긴다"
   규칙을 장중 연속 스냅샷으로 일반화한 것이다. 최신이 무효화되면 직전 유효 스냅샷이
   규칙에 의해 자동 노출된다 — 별도 fallback 로직을 두지 않는다. 이때 재노출되는 직전
   스냅샷은 승계로 밀려났던 낡은 설명일 수 있다 — 이를 수용하는 근거는 ① 응답에
   `explanation_as_of`를 노출해 기준 시점이 드러나고(구현은 선행 요소인 후속 ②에,
   계약 명문화는 ⑤에 포함) ② 재노출분도 결정
   2-(b)·(c) 검증 대상이라 시세와 어긋나거나 최대 연령을 초과하면 무효화되기 때문이다.
   다음 스냅샷이 곧 도착한다는 보장은 없지만(런 실패·재트리거 부재), 그 경우의 노출
   낡음은 결정 2-(c) 만료 트리거가 상한한다 — 임계값은 검증 시스템 설계(후속 ⑦)의
   **필수 결정 항목**이다. superseded 별도 상태는 두지 않는다.
4. **검증 대상은 현재 PUBLISHED 전체다** (노출 중 head 한정이 아니다). 테넌트별 폴링·소비
   시점이 달라 어느 스냅샷이 어느 증권사에 노출 중인지 Cloud가 단정할 수 없다. 검증이
   트리거를 확인하면 PUBLISHED→WITHDRAWN 전이 + INVALIDATION 발번으로 그 결과의 NEW 를
   받은 테넌트에 전파한다(원본 미수신 테넌트 제외 — sync-protocol 의 gap 계약 보존,
   ALPHA-440 구현 확정). 검증 시스템의 상세 설계(주기·검사 방법·비용 상한과 retention)는
   후속 문서로 분리한다.
5. **순서 제약: INVALIDATION 발번(ALPHA-440)과 검증 시스템 v1(방향 대조 — 노출 낡음
   목표 상한 포함)이 게이트 폐지에 선행한다.** 오류 스냅샷을 물릴 수단 없이 발행 빈도만
   올리면 오류 노출 빈도도 같이 오르고, 수단이 있어도 탐지 주체(검증 v1)가 없으면 결정
   2의 트리거를 확인할 눈이 없어 오류·낡은 fallback이 무기한 노출될 수 있다. 온프렘
   다스냅샷 수용(결과 절)까지 포함해 선행 3요소가 모두 갖춰진 뒤에만 게이트를 제거한다.

## 대안

- **하루 1회 유지** — 제품 목표(실시간 지향)와 불일치. 배제.
- **방향 불일치를 다음 스냅샷 승계로만 해소(무효화 없이 대기)** — 다음 런까지의 공백 동안
  시세와 반대 방향의 설명이 계속 노출된다. 오도 콘텐츠를 즉시 내릴 수단이 없어 배제
  (결정 2-(b)).
- **검증을 노출 중 head에 한정** — 비용은 낮지만 테넌트별 노출 시점 차이를 무시한다.
  Cloud가 노출 여부를 단정할 수 없는 스냅샷을 무검증으로 두게 되어 배제(결정 4).

## 결과

- **ADR-0044와의 관계**: 대체가 아니라 확장이다 — 0044가 이연한 발번 정책을 채운다.
  0044의 "소멸성 콘텐츠라 재게시 무익" 전제는 부분 재해석된다: 정정 재게시는 여전히
  무익하므로 CORRECTION 폐지는 유지되고, 장중 스냅샷 승계는 정정이 아니라 새 게시이므로
  유익하다.
- **Cloud 스키마 변경 불요**: 게시 grain `(etf_instrument_id, trade_date,
  explanation_as_of)`의 부분 유니크(`uq_explanation_result_published_grain`)는 as_of가
  다르면 복수 PUBLISHED를 이미 허용한다
  ([event-bundle-schema.md](../contracts/event-bundle-schema.md)). cloud 게이트 폐지는
  코드 변경만이다.
- **온프렘은 다스냅샷 수용 작업이 선행돼야 한다**: `publication`의 day-grain 부분 유니크
  `uq_publication_published_grain (etf_ticker, trade_date)`와 screening-worker
  `publish()`와 tenant-console-api `publish()` **두 곳**의 grain 선점 가드가 장중 2번째
  스냅샷의 **게시를 생략**하며 — 형태와 실패 양상이 다르다: screening-worker는 `NOT
  EXISTS` 가드라 skip이 지속되고(INFO 로그 `grain 선점 시 skip` — 현행 day-grain에선
  의도된 동작), tenant-console-api는 이 부분 유니크를 arbiter로 한 `ON CONFLICT ... DO
  NOTHING`이라 인덱스만 바꾸고 arbiter를 갱신하지 않으면 추론 실패로 승인 트랜잭션이
  깨진다 —,
  publication-api 표시 정렬은 `published_at` 기준이라 결정 3(`explanation_as_of` 기준)과
  어긋난다. 유니크 확장(expand-contract)·가드 제거·정렬 전환 없이 cloud 게이트만 폐지하면
  이 결정이 약속한 장중 승계가 온프렘 노출에 도달하지 않는다.
- **후속 작업**: ① INVALIDATION 발번(ALPHA-440 — 선행 조건) ② 온프렘 다스냅샷 수용
  (`uq_publication_published_grain` 확장·두 publish() 정리 — screening-worker `NOT
  EXISTS` 가드 제거와 tenant-console-api `ON CONFLICT` arbiter 갱신·표시 정렬 as_of
  전환·`explanation_as_of` 응답 노출 — cloud 게이트 폐지에 선행) ③ `eventstore.py` day-grain
  게이트 제거와 `run_reason "DAILY"` 재검토 — 결정 5의 선행 3요소(①·②·⑦의 v1) 완료
  후에만 ④
  [event-bundle-schema.md](../contracts/event-bundle-schema.md)·
  [state-machine.md](../domain/state-machine.md)의 day-grain 서술 갱신과
  [sync-protocol.md](../contracts/sync-protocol.md) 규모 가정(일일 이벤트 수)의 실시간
  카디널리티 재산정(폴링 주기·limit 상한 포함) ⑤ 표시 규칙의
  [publication-api.md](../contracts/publication-api.md) 명문화 — `explanation_as_of`
  응답 노출 추가 포함(결정 3의 fallback 수용 근거 ①의 실체) ⑥ 파이프라인 스케줄 축
  (평일 15:40 cron·Reconciler 슬롯, `infra/terraform/modules/data-pipeline/`)의 장중 다회
  실행 전환 — 별도 티켓 ⑦ 검증 시스템 설계 문서 — 구현은 단계 순서를 따른다: **v1 방향
  대조 + 만료 검사**(저장된 방향 vs 현재 시세 부호·최대 연령 초과 무효화(결정 2-(c)) —
  임계값은 실효 노출 시간(최대 연령 + 검증 주기·지연 + 테넌트 전파 지연) 기준으로
  정량화, 선행: 게시
  시점 방향·기준 수익률 구조화 저장 + Cloud 측 실시간 시세 소스) → **v2 재유도 대조**(as_of 기준 인과 수익률을 현재 원장으로
  재유도해 게시 당시 값과 오차 대조 — 정정 "사건 감지"가 아니라 멱등 상태 비교) → **근거
  붕괴(뉴스 정정·철회)는 이연**(전파는 lineage join으로 쉬우나 감지 신호가 파이프라인에
  없고, 실시간 모델에선 다음 스냅샷 승계·v1/v2 가드가 대부분 흡수).
- **트레이드오프**: 운영 재실행·디버깅 런도 즉시 테넌트에 노출된다(스냅샷 승계가 곧 제품
  동작이므로 수용). 무효화 남발 시 노출 공백이 잦아질 수 있어, 검증 트리거 (b)의 판정
  기준(방향 불일치의 폭·지속 시간)은 검증 시스템 설계에서 정량화한다.
- 결정 주체·경위: 사용자 확정(2026-08-01) — 실시간 제공 목표 재확인에 따른 발번 정책 결정.
