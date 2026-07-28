# ADR-0042: sync Pull 응답을 공통 응답 포맷으로 통일 — 신규 없음 204 폐지

- 상태: 승인됨
- 날짜: 2026-07-28 (채택 2026-07-28)

## 맥락

[ADR-0040](0040-sync-integrity-mvp-to-signing.md)으로 sync Pull(`tenant-sync-api` `GET /api/v1/sync/bundle`)의 200 응답은 공통 응답 포맷(`ApiResponse<EventBundle>`, 번들은 `result` 아래)이 됐다. 그러나 "신규 없음"은 여전히 **204 No Content(무바디)** 다 — 204는 ADR-0040 이전(초기 walking skeleton)부터의 결정이라 그 재검토 범위에 들지 않았고, 이번에 독립적으로 재검토한다.

이 이형이 만드는 문제:

- **플랫폼 관례에서 벗어난 이형 성공 경로.** 컨트롤러 표준은 `ApiResponse<T>` 직접 반환(항상 200)인데 Pull 은 두 형상(200 + 포맷 / 204 무바디)을 갖고 `ResponseEntity` 로 상태를 수동 제어한다. 실제로 "왜 이 엔드포인트만 응답 포맷이 다른가"라는 혼란이 발생했다(2026-07-28 리뷰 논의). 같은 204 이형이 `publication-api` 의 설명 조회(`ExplanationController` — 조회 대상 부재 시맨틱)에도 있으나, 그쪽은 폴링 계약이 아니라 성격이 달라 **이 ADR 의 범위는 sync Pull 로 한정**한다(publication 쪽 재검토는 필요 시 별도).
- **204는 fail-silent 다.** 바디 없는 204는 소비자가 검증할 수 있는 것이 없다. 오설정된 프록시·게이트웨이·라우트가 204를 흘리면 `sync-agent` 는 이를 "신규 없음"으로 읽고 **sync 가 조용히 영구 정지한다** — at-least-once Pull 파이프라인에서 정상 상태와 구분되지 않는 최악의 고장 모드이고, 저장소가 도처에서 강제하는 fail-loud 원칙(AGENTS Rule 12)과 상충한다. 반면 "성공은 항상 `isSuccess:true` 포맷"이면 빈 응답조차 자기 형상을 증명해야 하므로 형상 위반이 즉시 표면화된다.
- **204의 실익이 없다.** 이 표면은 알려진 소비자 하나(`sync-agent`)뿐인 내부 M2M 폴링이다 — 계약 기본 주기 1~5분([sync-protocol.md](../contracts/sync-protocol.md), 데모 compose 는 시연용 5초 override). 204가 절약하는 것은 빈 포맷 바디(~100B)의 파싱뿐인데, 소비자는 어차피 200 경로의 파서를 갖고 있고 이 폴링 빈도에서 그 비용은 측정되지 않는다(주기가 길수록 더더욱). 공개 API·CDN 캐시 계층·고빈도 트래픽 같은 204 우위 조건이 하나도 없다.
- **소비자 분기 수는 동일하다.** "없음"의 표현이 상태 코드(204/200)든 `result` 유무든 분기는 하나다. 분기 위치를 고를 수 있다면 플랫폼 관례(공통 응답 포맷)를 따르는 쪽이 맞다.

## 결정

- **Pull 성공은 항상 200 + 공통 응답 포맷(`ApiResponse`)이다.** 번들 있음 = `result` 아래 `EventBundle`(현행 유지). **신규 없음 = `result` 필드 생략**(`isSuccess:true`·`COMMON200`, `result` 는 `@JsonInclude(NON_NULL)` 이라 null 이면 자동 생략된다). 컨트롤러는 `ApiResponse<EventBundle>` 직접 반환으로 회귀한다(`ResponseEntity` 제거 — 플랫폼 관례 복원).
- **"빈 번들은 만들지 않는다" 원칙은 유지된다.** 빈 `entries` 의 EventBundle 을 조작하는 것이 아니라 `result` 부재가 "번들 없음"을 말한다 — `cursor_from`/`cursor_to` 를 지어내지 않으므로 committed cursor 전진 시맨틱은 무관하다.
- **소비자 분기 = `result` 유무.** `result` 가 있으면 기존 fail-loud 검증(cursor·entries)이 불변으로 적용되고, 없으면 저장·전진 없이 정상 no-op 이다.
- **모든 성공 응답이 형상을 증명한다(fail-loud).** 전환 완료 후 204·무바디·비포맷 수신은 계약 위반으로 표면화한다(`UPSTREAM_MALFORMED` 계열). 현행 `sync-agent` 의 "본문 없는 200은 형상 위반" 가드는 "포맷 아닌 성공 응답은 전부 위반"으로 강화되는 셈이다.
- **`sync-agent` 릴레이 표면(`ResponseEntity<byte[]>`)은 이 ADR 의 범위 밖이다.** 이 ADR 은 Pull 의 공개 계약(cloud↔sync-agent)과 그 소비를 다루고, 릴레이는 수신 바이트를 재직렬화 없이 내부망에 넘기는 전달 수단이다(현행 구조 유지 — 형식 재검토가 필요해지면 별도 논의). 다만 상류가 204를 내지 않게 되므로 릴레이의 204 분기는 소멸하고 항상 200 릴레이가 된다.

## 대안

1. **현행 유지 — 신규 없음 204.** HTTP 시맨틱으로는 정확하다("성공, 줄 바디 없음"). 그러나 위 맥락의 fail-silent 고장 모드와 sync Pull 의 이형 성공 경로를 남기고, 이 표면의 조건(내부 M2M 단일 소비자)에서 얻는 실익이 없다.
2. **`SuccessStatus.NO_CONTENT`(COMMON204) 활용 — 포맷을 유지한 채 204.** jvm-common 에 `HttpStatus` 를 실은 enum 과 `ApiResponse.of(BaseCode, result)` 가 이미 있어 그 흐름으로 풀자는 안. 검토 결과 불성립: `of()` 는 `getReasonHttpStatus()` 에서 code·message 만 취하고 **httpStatus 는 버리며 호출처가 0** 이고, 성공 경로에 상태를 적용하는 장치 자체가 없다(상태 적용은 에러 경로의 `ExceptionAdvice` 뿐 — 성공용 `ResponseBodyAdvice` 신설이 필요). 결정적으로 **204는 RFC 9110상 바디를 갖지 않으므로 "공통 응답 포맷을 실은 204" 는 원리적으로 모순**이다. 프레임워크에 남은 `NO_CONTENT` enum 은 이 표면과 무관한 장식이다.
3. **200 + 빈 `entries` 번들.** 컬렉션 관례("빈 목록은 200 + `[]`")의 직역. 그러나 번들은 목록이 아니라 커서 범위를 주장하는 집합체다 — 담은 것이 없는데 `cursor_from`/`cursor_to` 를 채우면 조작값이 되고, 소비자는 이 값으로 committed cursor 를 전진시키므로 재개점이 오염된다. 기각.

## 결과

- **마이그레이션은 확장-수축 3단계다** (ADR-0040 T1→T2→T4 패턴 재사용, 구현 티켓: M1=ALPHA-598 · M2=ALPHA-599 · M3=ALPHA-600):
  - **M1 — 온프렘 소비자 관용(확장):** `intake` 가 `result` 생략 200 을 "신규 없음"으로 추가 수용한다(204 수용 유지). 데모 박스 배포.
  - **M2 — cloud 생산자 전환:** `SyncBundleController` 의 204 제거, 항상 200. `openapi.yaml`·[sync-protocol.md](../contracts/sync-protocol.md)("신규 없음: 204" 조항) 동반 갱신. dev 머지 시 cloud CD 자동.
  - **M3 — 온프렘 정리(수축):** `sync-agent`(`BundleRelayService`·릴레이 204 경로)와 `intake`(`SyncAgentClient`)의 204 분기 제거 — 이후 204 수신은 계약 위반으로 표면화. 데모 박스 배포.
  - 순서 위반(M1 전에 M2) 시 데모 박스가 신규 없음 틱마다 오류를 기록한다(cursor 오염은 없으나 정상 no-op 을 오류로 오독).
- **과도기 비용을 다시 치른다.** ADR-0040 T4로 이중형상 과도기를 막 걷어냈는데 이 변경이 다시 3단계 롤아웃(과도기 이중 수용)을 요구한다. 대면 트래픽이 없는 데모 단계가 이 비용이 가장 쌀 때라는 판단이다.
- **screening 은 무관하다.** 신규 없음 응답은 저장되지 않으므로 `received_bundle` 저장분은 항상 `result` 가 있는 포맷이고, `DeliveryBundleParser` 의 검증(minItems=1 포함)은 불변이다.
- **용어:** 이후 문서·주석은 "공통 응답 포맷(`ApiResponse`)" 표기를 쓰고, 구현 단계(ALPHA-599)에서 계약 문서(sync-protocol·event-bundle-schema·openapi)의 "봉투" 표기도 이 용어로 정리한다.
