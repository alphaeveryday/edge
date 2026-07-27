# ADR-0040: Sync 번들 무결성 — 체크섬·byte[] 응답을 MVP에서 목표 계약(서명)으로 이관

- 상태: 승인됨
- 날짜: 2026-07-27

## 맥락
[[0015-sync-protocol-mvp]]는 MVP 스펙에 **SHA-256 체크섬**을, 목표 계약에 **번들 서명**을 뒀다.
구현이 이를 따라 `SyncBundleController`가 번들을 `byte[]`로 반환하고 `X-Bundle-Checksum:
sha256=<hex>` 헤더를 실으며, `BundleSerializer`가 직렬화를 한 번만 수행해 그 바이트로 SHA-256을
계산한다(재직렬화 금지 — 성공 응답에 공통 `ApiResponse` 봉투를 씌우지 않는 이유가 이것이다).

두 전제가 그사이 확정됐다:
- **인증은 mTLS로 확정** — 인증서-테넌트 바인딩([[0012-sync-cert-bootstrap]]), 전용 mTLS ALB로
  전송 종단([[0034-host-per-edge-alb]]). 즉 채널은 TLS로 무결성·기밀성이 보장되고 상대가 인증된다.
- **서명은 후순위** — 벤더 개인키 기반 번들 서명은 목표 계약으로 문서상만 존재하고 **전용 티켓이
  없으며**, 에픽 ALPHA-420에서 명시적으로 "범위 밖"이다.

이 전제(TLS 채널 + 서명 후순위)에서 "발신자가 제공하는 SHA-256 체크섬"이 **오늘 독립적인 기술
근거를 갖는지**를 재검토해야 한다. 후보 근거를 전수하면 아래처럼 **대부분 무너진다** — 유일하게
남는 것(종단 간 오염 탐지)은 존재하되 MVP 필수 근거로는 약하며, 결과의 "잔여 리스크"에서 따로 다룬다:

- **전송 중 오염** — TLS 레코드 MAC이 이미 잡고 연결을 끊는다(중복).
- **절단·부분 응답** — 잘린 JSON은 닫는 괄호 전에 EOF라 **파싱에서 실패**하고, HTTP 프레이밍
  (Content-Length·chunked)도 조기 EOF를 감지한다. 통과하더라도 커서를 전진시키지 않고 다음 주기에
  재-Pull하므로 자가치유된다(at-least-once + 멱등 upsert).
- **감사·부인방지** — 양측이 똑같이 계산할 수 있는 맨 해시는 "벤더가 발행한 원본"을 증명하지
  못한다. 감사급 보증은 **서명**(개인키 귀속)·정합(건수·시퀀스)·불변 저장의 몫이다.
- **dedup** — 계약이 dedup을 **cursor 기준으로 명시**했다(바이트 기준 아님). 재-Pull은 `bundle_id`·
  `generated_at`를 새로 발번해 같은 범위라도 바이트·체크섬이 달라지므로, 체크섬은 dedup에 쓰이지
  않는다(docs/contracts/sync-protocol.md).
- **직렬화 버그** — 체크섬은 직렬화 *이후*의 바이트를 해싱하므로 버그 든 바이트를 충실히 해싱할
  뿐, 버그를 잡지 못한다.

`byte[]` 응답은 그 자체가 목적이 아니라 **체크섬 대상 바이트를 재직렬화로 어긋나지 않게** 하려고만
존재한다. 수신 원본 보존(replay·디버깅)은 소비자(Sync Agent)가 받은 바이트를 저장하면 되는 일이라
발신자 체크섬·서버 `byte[]`와 분리된다.

## 결정
체크섬과 `byte[]` 응답을 **MVP에서 제외하고 번들 서명과 함께 목표 계약으로 이관**한다.

- **MVP**: sync 번들을 다른 엔드포인트와 동일하게 **`ApiResponse<EventBundle>` 타입 JSON**으로
  반환한다. `X-Bundle-Checksum` 헤더와 `BundleSerializer`의 SHA-256 경로는 제거 대상이다.
- **무결성 위임(한계 명시)**: 전송 무결성은 mTLS/TLS, 절단은 HTTP 프레이밍 + 파싱 실패 시 재-Pull이
  맡는다. 단 TLS는 ALB에서 종단되므로 이는 **종단 간 보증이 아니다**(멱등 재-Pull은 재시도 중복을
  흡수할 뿐 오염을 탐지하지 않는다) — 남는 잔여는 아래 결과에 명시하고 수용 근거를 단다.
- **목표 계약**: byte-exact가 실제 요구가 되는 시점 — 즉 **번들 서명 착수 시** — 에 수신 바이트 원본
  보존과 서명 검증을 함께 도입한다. 그때 "받은 바이트 바로 그것"을 보존·검증할 정당한 이유가 생긴다.
- **[[0015-sync-protocol-mvp]] 승계**: 이 ADR은 ADR-0015를 **승계(supersede)하여 sync 프로토콜
  MVP 결정의 현행 권위**가 된다. 실제로 바뀌는 건 체크섬(+`byte[]`) 한 항목뿐이지만, README 상태
  어휘에 "부분 정정" 값이 없어 살아 있는 결정을 오해 없이 표기할 방법이 승계밖에 없다. 따라서 ADR-0015의
  **cursor 기반 delta·폴링 1~5분·at-least-once/멱등 upsert·gap 감지(목표)** 결정을 이 ADR이 **그대로
  승계**하고(아래가 현행 결정의 전부다), 체크섬(+`byte[]`)만 목표 계약으로 옮긴다.

## 대안
- **현행 유지(체크섬 MVP 존치)** — 근거는 "저비용 + 미래 서명 대비"다. 그러나 근거 없는 기능을
  MVP에 싣는 것은 Rule 2 위반이다. 완전성 트립와이어 이득은 **대부분** 파싱 실패 + 재-Pull과 겹치고,
  겹치지 않는 잔여(평문 홉 오염 탐지)는 **확률이 낮고 체크섬이 그 위험을 좁게만 커버**해 수용한다 —
  단 피해 반경 자체는 고객 대면이다(결과의 "잔여 리스크" 참조). byte-exact 보존은 서명 착수 시점에
  `byte[]`+서명을 함께 넣어도 소급 비용이 크지 않다. **단, 감사·데이터 계보(lineage)가 증권사 연동의
  *오늘의* 컴플라이언스 요구로 확인되면** byte-exact 보존은 정당해지고 이 정정은 철회 대상이 된다 —
  그 경우 체크섬이 아니라 서명으로 바로 가는 것이 옳다. 또한 이 대안의 실질 무게는 **제거 비용**이다:
  체크섬·byte[]·`received_bundle`(body+checksum) 원본 저장은 walking skeleton(sync-agent·intake·
  screening-worker)에 **이미 구축·동작 중**이라, 제거는 3개 모듈 + DB 마이그레이션을 걷어내는 실작업이고,
  그 원본 저장은 장차 서명이 요구할 **바이트-정확 substrate**다 — 지금 제거했다가 서명 시점에 재구축하는
  왕복이 이 판단의 핵심 트레이드오프다.
- **지금 서명까지 당겨 구현** — 서명은 PKI·키 배포·감사 요구가 실재해야 값을 하는데 전용 티켓조차
  없다. MVP를 막을 사안이 아니라 후순위 유지가 맞다.
- **체크섬만 빼고 `byte[]`는 유지** — `byte[]`의 유일한 목적이 체크섬 바이트 정합이라 함께 제거가
  맞다. 원본 보존이 필요하면 소비자가 수신 바이트를 저장하면 되고 서버 응답 형식과 무관하다.

## 결과
- **후속 작업 범위(채택 시, 별도 티켓)** — 이 변경은 서버 한 곳이 아니라 이미 구축된 sync 경로 전체에
  파급된다. 이 ADR은 결정만 기록하고 구현은 후속 티켓이다:
  - ① Cloud `SyncBundleController`(byte[]→봉투 반환)·`BundleSerializer`(SHA-256 경로 제거). **주의**:
    `BundleSerializer`가 유일하게 설정하는 `PropertyNamingStrategies.SNAKE_CASE`도 함께 사라진다 —
    `EventBundle`에 필드 애너테이션이 없고 전역 Jackson naming 설정도 없어, Spring 기본 직렬화로 바꾸면
    `bundleId`·`cursorFrom`처럼 camelCase가 나가 와이어 계약·intake 파서(`cursor_from`)가 깨진다.
    봉투 내부 번들의 snake_case를 전역 `spring.jackson.property-naming-strategy` 또는 DTO 애너테이션으로
    **대체 유지**하는 것을 범위에 명시해야 한다.
  - ② On-Prem `sync-agent` — `BundleRelayService`가 수신 바이트로 SHA-256을 계산해 `X-Bundle-Checksum`과
    대조하고 불일치 시 `CHECKSUM_MISMATCH`로 거부한다. 헤더가 사라지면 **모든 번들이 거부**되므로 이
    검증과 `BundleRelayController`의 헤더 릴레이를 함께 걷어내야 한다.
  - ③ `intake` `BundleIngestor`는 봉투 **본문 루트**에서 `cursor_from`·`cursor_to`를 `readTree().path()`로
    읽는다 — 본문이 `ApiResponse.result`로 감싸이면 두 필드가 결측이라 형상 위반 fail-loud로 떨어진다.
  - ④ `screening-worker` `DeliveryBundleParser`는 저장된 body 루트에서 `readTree(body).path("entries")`로
    `entries`를 읽는다 — 봉투로 감싸이면 루트에 `entries`가 없어 매 번들이 계약 위반 예외로 실패하고
    스크리닝이 마킹 없이 무한 재시도된다. 파서와 계약 테스트를 함께 갱신해야 한다.
  - ⑤ `received_bundle.checksum`(VARCHAR(72) NOT NULL + CHECK `^sha256=…`) 제거는 단일 마이그레이션이
    아니라 **expand→transition→contract** 순서다([[0005-db-as-contract]] — 신구 앱이 DB를 공유하므로):
    **(expand)** 컬럼은 남기고 `NOT NULL`·CHECK 제약만 푸는 마이그레이션, **(transition)** 체크섬을 읽고
    쓰는 코드 전부 배포·은퇴(sync-agent `BundleRelayService`, intake `BundleIngestor`·
    `ReceivedBundleRepository`·`ReceivedBundle` 엔티티), **(contract)** 후속 마이그레이션으로 컬럼 drop +
    `generated` 스키마 아티팩트(`physical-erd-onprem.dbml`) 갱신. 순서를 어기면 배포가 깨진다 — 컬럼 선-제거
    시 구 intake의 INSERT·Hibernate validate 실패, `NOT NULL` 유지 상태에서 체크섬 생략 writer 배포 시 전
    번들 거부.
  - ⑥ 계약 문서(sync-protocol.md·event-bundle-schema.md) **및 `tenant-sync-api/openapi.yaml`** — 200 응답
    스키마를 `ApiResponse` 봉투로 감싸고 `X-Bundle-Checksum` 헤더 선언을 제거한다(현재 71–82행이
    `EventBundle`을 직접 참조하고 헤더를 선언 — README가 이 파일을 경로·필드·타입 문법 SSOT로 지정하므로,
    갱신하지 않으면 코드 생성기·계약 소비자가 이전 형상을 받는다).
  - ⑦ **롤아웃 순서(요지 — 상세 runbook은 구현 티켓 소관)**: Cloud와 온프렘이 원자적으로 배포되지
    않으므로 와이어 계약·저장 데이터 모두 staged **expand→transition→contract**다. 소비자(sync-agent·
    intake·screening)를 **구·신 본문 형상 + 선택적 헤더를 모두 수용**하도록 먼저 배포 → Cloud 생산자를
    봉투로 전환 → 기존 `screened_at IS NULL` direct-root 저장분이 소진된 뒤 구 형식 지원 제거. 순서를
    어기면 Cloud 선전환 시 구 `BundleRelayService`가 헤더 결측을 거부하거나 구 파서가 `result` 래핑을
    못 읽고, 미전환 저장 행 하나가 `ScreeningPoller`(첫 실패서 순서보존 중단) 전체를 막는다.
- 갱신 대상(채택에 따라 — 구현 에픽 ALPHA-582): docs/contracts/sync-protocol.md(무결성·응답 포맷 절)·
  event-bundle-schema.md(체크섬 절)는 T2에서, 에픽 ALPHA-420 DoD의 "번들 체크섬(SHA-256) 무결성 검증"
  항목은 목표 계약으로 이동(T0에서 반영).
- **잔여 리스크(수용, 재평가)**: 체크섬은 **`sync-agent` 수신 시점(`BundleRelayService`)에 1회만** 검증되고
  이후 재검증은 없다(intake·screening은 저장된 body를 그대로 파싱). 따라서 체크섬이 실제로 지키는 구간은
  **와이어(클라우드 직렬화→agent 수신)**뿐이고, 그중 무방비 구간은 **ECS→ALB 평문 홉 하나**다(나머지
  와이어는 TLS. [[0034-host-per-edge-alb]], `infra/terraform/modules/alb/main.tf` target protocol=HTTP).
  - **피해 반경 정정**: 이전 판의 "비개인화 메타데이터라 반경 좁음"은 **틀렸다**. 번들 본체
    (`explanation_result`)는 **고객 노출 후보 문구**이고(event-bundle-schema.md), walking skeleton에서 NEW는
    스크리닝을 거쳐 **AUTO_PUBLISHED**로 자동 게시된다(`BundleScreener`·`PolicyEvaluator`). 이 평문 홉에서
    `summary` 등이 **유효 JSON으로 오염**되면 고객 대면 게시까지 도달할 수 있어 반경은 **고객 대면**이다.
  - **그럼에도 수용하는 근거**: (a) 확률이 지극히 낮다(단일 in-VPC TCP 홉의 TCP/이더넷 무결성, 유효
    JSON을 내는 오염은 드묾). (b) 더 본질적으로 — **체크섬은 이 위험의 올바른 방어가 아니다.** 고객 대면
    오염 표면의 대부분(agent 수신 *이후*의 저장·screening·게시)은 체크섬이 애초에 안 지킨다. 와이어 한 홉만
    막는 발신자 체크섬으로 고객 대면 콘텐츠 무결성을 보증한다는 명제 자체가 성립하지 않는다. 그 보증이
    실제로 필요하면 답은 **게시 지점까지의 종단 간 무결성**이고 그 수단이 서명(진정성까지 함께)이다 —
    단 서명도 **게시 경계에서 재검증하거나 서명 원본↔게시 데이터의 암호학적 계보를 유지**해야 성립한다.
    수신 시점 1회 검증에 그치면 오늘 체크섬과 똑같은 한계(수신 후 오염 미탐지)를 갖는다. 따라서 목표
    계약은 단순 "서명 도입"이 아니라 **"게시 지점까지 검증되는 서명"**을 요구해야 하며, 이 ADR이 미루는
    대상이 바로 그것이다.
  - 요컨대 체크섬 제거로 늘어나는 노출은 "평문 한 홉의 저확률 오염"으로 좁게 한정되며, 위험의 본체
    (종단 간)는 체크섬 유무와 무관하게 서명 도입 전까지 남는다.
- **[[0015-sync-protocol-mvp]] 상태 전환**: README 규약대로 ADR-0015 상태를 **`대체됨(→
  ADR-0040)`으로 표시**한다(본 채택에서 반영 — 승인된 결정이 바뀔 때의 표준 처리). 위 "승계"에서 ADR-0015의 유효 결정
  (cursor·폴링·at-least-once/멱등·gap)을 이 ADR이 그대로 재수록하므로, 상태 열의 `→` 포인터를 따르면
  현행 sync 프로토콜 MVP 결정 **전부를 ADR-0040 하나에서** 확인한다. 상태 열만 보는 독자가 체크섬을
  여전히 MVP 요구로 오해할 여지가 없다(대체됨이 승계본을 가리키므로 살아 있는 결정도 유실되지 않는다).
- **채택 결과** — 200 성공 경로가 프로젝트 나머지 API와 균질한
  봉투(`ApiResponse`)가 되어 체크섬 헤더·`byte[]` 특례가 사라진다. 다만 **204(신규 없음) 계약은 유지**
  되므로 소비자는 여전히 상태 코드로 분기한다(200이면 봉투 파싱, 204면 스킵) — 봉투 일관성이 개선될
  뿐 상태 코드 선분기를 없애지는 않는다.
