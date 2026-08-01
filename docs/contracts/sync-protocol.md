# Sync 프로토콜 계약 (확정 결정)

> **계약 문서** — 이 파일의 변경은 진기·영서 공동 승인 대상이다(CODEOWNERS). 인터페이스 계약의 정의는 [event-bundle-schema.md](event-bundle-schema.md).

> **문법 명세(기계가독)** — 경로·필드·타입의 기계가독 명세는 [tenant-sync-api/openapi.yaml](../../src/apps/cloud/tenant-sync-api/openapi.yaml)(OpenAPI 3.1). 이 문서(시맨틱 계약)가 상위, openapi 는 하위 문법 층이다(TODO §5 2층 구조) — 의미가 충돌하면 이 문서가 이긴다. 번들 필드 내부 형상은 [event-bundle-schema.md](event-bundle-schema.md).

**MVP 구현 스펙**:

- **Cursor**: 테넌트별, Cloud 서버가 발번하는 단조 증가 sequence. 온프렘은 마지막 처리 cursor를 durable 저장하고 `?after={cursor}`로 Pull한다. Pull을 개시하는 주체는 DMZ의 **Sync Agent**이고, 권위 committed cursor의 durable 저장 주체는 2모듈 표준에서 **Intake(내부망)**·단일 모듈 옵션에서 sync-agent다([ADR-0036](../adr/0036-sync-agent-intake-topology.md)). 2모듈에서 Pull 재개점은 **Intake의 committed cursor가 권위**다 — Sync Agent의 DMZ-local 마크는 Intake의 **commit-ack까지만 전진**(내부 채널 ack)하므로, 전송·commit 실패 시 그 번들을 재-Pull하고 멱등 upsert가 중복을 dedup해 **유실 없이** 수렴한다. ack 없이 마크를 먼저 전진시키면 `?after=`가 미commit 번들을 건너뛰어 유실되므로 ack가 정확성 필수다(ADR-0036).
- **Cursor 발번 시점 (확정, 2026-07-13)**: 공통 이벤트는 비개인화 단일 레코드지만 cursor는 테넌트별이므로, **테넌트별 outbox fan-out 시점**(공통 이벤트 1건 → 테넌트별 전달 레코드 N건 생성)에 각 테넌트의 sequence를 발번한다. Event Bundle은 이 outbox를 cursor 순으로 묶어 생성한다. 이 fan-out 규칙은 "Cloud Event Store 스키마 + 번들 생성 규칙" 인터페이스 계약([event-bundle-schema.md](event-bundle-schema.md))의 일부이므로 변경 시 진기-영서 양자 합의 대상.
- **전송 단위**: Event Bundle (신규 이벤트 + 무효화 이벤트 포함 — 정정(CORRECTION)은 폐지, [ADR-0044](../adr/0044-correction-abolition.md)).
- **무결성**: 전송 무결성은 mTLS/TLS(전송 계층). 앱 레벨 SHA-256 체크섬·byte[] 응답은 목표 계약(서명)으로 이관됐다([ADR-0040](../adr/0040-sync-integrity-mvp-to-signing.md)).
- **폴링 주기**: 기본 1~5분 (테넌트 설정 가능).
- **전달 보장**: at-least-once. 중복·재-Pull(재시도·ack 유실 포함) 수신은 **무해해야 한다** — 요구사항은 ① 유실(skip) 없음 ② immutable raw 레코드 손상·충돌 없음이다.
  - **도메인 저장**: 도메인 ID(`explanation_result_id` 등 — [event-bundle-schema.md](event-bundle-schema.md) ID 체계) 기반 **멱등 upsert**.
  - **raw 번들 저장 dedup은 cursor 기준**(바이트 기준 아님) — 재-Pull 응답은 `bundle_id`·`generated_at`를 매 응답 새로 발번해 같은 범위라도 바이트가 달라지므로, 바이트로 dedup하면 immutable raw(cursor 키)를 덮어쓰거나 PK 충돌한다. **dedup 키·부분 겹침 처리 (확정, 2026-07-22 — walking skeleton 관통으로 실증)**: dedup 키는 **번들 `cursor_from`**(received_bundle PK — 같은 재개점의 재-Pull 은 같은 `cursor_from` 이라 `DO NOTHING` 으로 수렴), **부분 겹침·cursor 역행은 fail-loud**(Intake 가 봉투 검증에서 즉시 실패 — 정상 경로에서 발생하지 않아야 하는 계약 위반 신호다). 토폴로지 결정([ADR-0036](../adr/0036-sync-agent-intake-topology.md))이 고정한 두 요구사항(유실 없음·raw 불변)을 이 메커니즘이 충족한다.
- **순서**: cursor 순 처리. 서버 발번 단조증가 cursor를 순차 소비하므로 무효화는 항상 대상 원본 이벤트보다 늦게 도착한다 (원본 cursor < 무효화 cursor). 무효화는 대상 원본을 `target_explanation_result_id`로 참조한다. "원본 미수신 상태의 무효화"는 gap(sequence 누락)에서만 발생할 수 있으므로, 보류-재처리 로직은 gap 감지(목표 계약)와 함께 도입한다. MVP는 순차 소비 보장 하나로 순서를 담보한다. (참고: 서버 발번 sequence 구조에서는 gap 감지가 수신 cursor의 불연속 확인만으로 가능해 구현 비용이 낮다 — walking skeleton 안정화 후 목표 계약에서 MVP로 조기 승격 검토 후보.)

**엔드포인트 계약 (확정, 2026-07-22)**:

> ALPHA-358 스캐폴드가 제안한 스펙을 walking skeleton 관통(ALPHA-405 — Tenant Sync
> API→Sync Agent→Raw Event Store)이 실증했고, 이 절로 확정 승격한다.

- **경로·파라미터**: `GET /api/v1/sync/bundle?after={cursor}&limit={n}`
  - `after` = 소비자의 마지막 committed cursor(첫 동기화는 0). 0 미만은 400.
  - `limit` = **응답 번들에 담길 전달 레코드(entry) 수 상한** — 생략 시 100, 허용 1..500, 범위 밖은 400(`SYNC4002`). 수치 표현 자체가 불가한 값(정수 범위 밖·비수치)은 파라미터 바인딩 실패라 공통 400 으로 나간다. 한 응답은 항상 번들 1개다(번들 개수 파라미터가 아니다).
- **응답 포맷 (200)**: 본문 = 공통 응답 포맷(`ApiResponse`, `{isSuccess,code,message,result}`)이고 Event Bundle은 `result` 아래에 온다([event-bundle-schema.md](event-bundle-schema.md)), `Content-Type: application/json`, 번들 필드 표기 snake_case(`bundle_id`·`cursor_from`·`delivery_type` …). 다른 엔드포인트와 동일한 공통 응답 포맷 규약이다([ADR-0040](../adr/0040-sync-integrity-mvp-to-signing.md) — byte[]·체크섬 특례 폐기, [ADR-0042](../adr/0042-sync-pull-uniform-response.md) — 204 특례 폐지).
- **다음 cursor 전달**: 번들의 `cursor_to`(`result.cursor_to`)가 재개점이다 — 소비자는 번들 commit(원본 저장 + cursor 전진)이 끝난 뒤 committed cursor를 `cursor_to`로 전진시키고, 다음 Pull 은 `after={committed cursor}` 로 요청한다. entry 별 `cursor`는 번들 내 순서와 도메인 반영 추적용이며 재개점이 아니다. 서버는 `after` 초과분을 cursor 오름차순으로 묶는다 — gap 감지는 목표 계약(아래).
- **신규 없음**: `200` + `result` 필드 생략 — 빈 번들은 만들지 않는다(`result` 부재가 "번들 없음"을 말한다, [ADR-0042](../adr/0042-sync-pull-uniform-response.md) — 204 는 검증 불가 fail-silent 라 폐지). `"result": null` 명시는 계약 밖(생산자는 필드를 생략한다).
- **무결성**: MVP는 전송 계층(mTLS/TLS)에 위임 — 앱 레벨 체크섬 헤더(`X-Bundle-Checksum`)는 폐기됐다([ADR-0040](../adr/0040-sync-integrity-mvp-to-signing.md)). 발신자 서명 기반 무결성은 목표 계약(아래).
- **에러 시맨틱**: 성공(200)·4xx/5xx 모두 공통 응답 포맷(jvm-common `ApiResponse`) — 구분자는 `isSuccess` 다(성공=true·번들 있으면 `result` 아래·신규 없음이면 `result` 생략, 에러=false·`result` 없음).
  - `400 SYNC4001`(after 위반)·`400 SYNC4002`(limit 위반) — **소비자 버그 신호**. 401·403·410 외 4xx 는 재시도로 낫지 않는다: 소비자는 이를 **일시 장애로 취급하지 않고**(백오프·복구 대기 없음) 즉시 에러 로그로 표면화하며, 해법은 수정 배포다. 폴링 데몬 구조상 다음 주기의 재호출 자체는 발생하지만 그것이 복구 수단이 아니다(fail-loud).
  - 5xx·네트워크 오류 — 일시 장애로 보고 다음 폴링 주기에 재시도한다. at-least-once 이므로 재시도 중복은 멱등 upsert 가 흡수한다.
  - 인증서 관련 401·403 의 도메인 코드는 [sync-auth.md](sync-auth.md) 계약에서 추가한다.

**일일 이벤트 규모 가정 (설계 입력, 2026-07-22)**:

> 실측 전의 설계 가정이다 — 트리거 분포 계측(ALPHA-452)이 쌓이면 실측으로 대체한다.
> 가정이 10배 이상 빗나가는 변화(커버리지 해외 확장 등)가 오면 이 절을 재검토한다.

- 커버리지는 국내 상장 ETF 한정([ADR-0024](../adr/0024-scope-domestic-etf.md)), 급등락 게이트를 통과한 종목만 설명이 생성된다 → **평시 테넌트당 일 수십 건** 수준.
- 피크는 시장 전반 급변동일에 유니버스 대부분이 트리거되는 경우로 잡는다 → **상한 가정 테넌트당 일 1,000건**(무효화 포함).
- 여유도: `limit` 500 × 폴링 1~5분이면 시간당 수천~수만 entry 처리 용량 — 피크 가정 대비 수십 배 이상이라 limit·폴링 주기는 병목이 아니다. 규모를 이유로 한 스트리밍·압축 도입은 실측 전에 하지 않는다.

**목표 계약 (문서상 명시, 구현은 후순위)**:

- MVP 스펙 + **벤더 개인키 기반 번들 서명** — 감사 시 "이 콘텐츠는 벤더가 발행한 원본"임을 증명. Raw Event Store에 서명 원본 보존. MVP에서 이관된 앱 레벨 무결성(체크섬·byte[] 원본 보존)이 여기 귀속된다([ADR-0040](../adr/0040-sync-integrity-mvp-to-signing.md)).
- 순서 보장 및 gap 감지(sequence 누락 시 재요청)의 명시적 계약화.
