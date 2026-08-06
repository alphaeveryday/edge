# Event Bundle 인터페이스 계약 (진기-영서)

> **계약 문서** — 진기-영서 인터페이스는 **Cloud Event Store DB 스키마**다([../adr/0026](../adr/0026-ownership-boundary-db.md), db-as-contract). 이 파일 중 스키마 경계면 서술의 변경은 공동 승인 대상(CODEOWNERS)이고, 번들 와이어 포맷(JSON·체크섬)은 Sync 양단 소유자(영서)의 스펙으로 함께 기록한다.

> **기계가독 계약(JSON Schema)** — 번들 JSON 구조의 기계가독 실체는 [`libs/schema/contracts/event-bundle.schema.json`](../../src/libs/schema/contracts/event-bundle.schema.json)(draft 2020-12, ALPHA-497). 이 문서(시맨틱·필드 정의)가 상위 SSOT이고 schema.json 은 그 기계가독 층이다. producer(tenant-sync-api)와 온프렘 소비자(screening-worker `BundleScreener` wire 적재·publication-api `ExplanationStore` 서빙 파싱)가 이 파일을 로드해 전 구간 계약 테스트로 검증하며(`contract-test.yml` CI), tenant-sync-api `openapi.yaml` 도 이 파일을 `$ref` 한다.

> **상태: 합의 진행 (v4, 2026-07-24)** — 물리 스키마(V202607150001)와 초안(ALPHA-356)을 병합했고, 전달 레코드(outbox)를 `tenant_delivery`로 확정했다(ALPHA-396). `source_events`·`evidences` 경계면 컬럼까지 확정했다(ALPHA-395, 아래 "경계면 컬럼" 절). 영서 단독 결정은 모두 확정됐고, 열린 항목은 진기 확인 대상(스키마 `tenant`·`tenant_credential` 정의, 선별 nullable 컬럼 채움 보증)뿐이다.

- 오너십 경계: **김진기** — Data Pipeline → Common Analysis Engine → **Cloud Event Store 적재까지** / **조영서** — DB를 소비하는 이후 전부: **Event Bundle 생성(tenant-sync-api의 DB 조회·조립)**, 전달 레코드, Sync Agent, 온프렘 ([../adr/0026](../adr/0026-ownership-boundary-db.md)).
- 전송 단위: Event Bundle (신규 + 무효화 — 정정(CORRECTION)은 폐지, [ADR-0044](../adr/0044-correction-abolition.md)). 프로토콜(엔드포인트·cursor·에러)은 [sync-protocol.md](sync-protocol.md).

## Cloud Event Store 스키마

**물리 정의는 [`src/libs/schema/migrations-cloud/`](../../src/libs/schema/migrations-cloud/)의 Flyway SQL이다** — generated 모델 생성기가 없는 현재는 이 SQL이 계약을 정의한다([implementation.md](../implementation.md) §4). 최초 도입은 `V202607150001__replace_analysis_mart_with_etf_explanation_schema.sql`(ALPHA-359, 47개 테이블, `public` 스키마), sync cursor 정정은 `V202607150002`(ALPHA-356). 이 경로는 CODEOWNERS로 이 문서와 같은 양자 합의 게이트에 묶여 있다.

**v4 아규먼트 축 확장(ALPHA-544, `V202607242020__add_event_argument_axes.sql`)**: `event_measure` 신규(값형 아규먼트) + `event_argument.slot·mention_text·entity_kind·group_ord` + `source_event.predicate_code·confidence_level·completeness`. 전부 additive(nullable 컬럼·신규 테이블)라 **경계면 컬럼 선별(ALPHA-395)과 번들 형상에 영향 없음** — 번들 미탑재, reader 무영향. 값·참여자 축의 번들 탑재 여부는 검수 UI 요구 발생 시 별도 합의.

아래는 Sync 채널이 실제로 소비하는 **경계면**만 추린 것이다. 47개 전체가 인터페이스는 아니다 — 나머지는 진기 측 내부 구현이며 양자 합의 없이 바뀔 수 있다.

### 번들에 실리는 것 (영서가 읽는 면)

| 테이블 | 역할 | 키 |
| --- | --- | --- |
| `explanation_result` | 고객 노출 후보 문구. 번들의 본체 | `explanation_result_id` |
| `explanation_run` | 어느 실행이 그 결과를 냈는지 + 사용한 릴리스 번들 버전 | `explanation_run_id` |
| `source_event` | 설명이 근거로 삼은 소스 이벤트 | `source_event_id` |
| `event_evidence` | `evidences` 문서로의 lineage 브리지(`source_event_id`·`assertion_id`) — 페이로드는 `document`가 공급 | `evidence_id`·`assertion_id` |
| `explanation_run_event_evidence` | 번들 `evidences` 이벤트 근거 갈래의 lineage — 어느 evidence 가 어느 `explanation_run` 에 속하는지 잇는 경로(공시 갈래는 아래 행). "내부 구현·자유 변경" 아님, 양자 합의 대상 (ALPHA-363). writer=analysis-engine, `stage_code` 는 현재 `PROMPT` 한 값뿐이다 — 설명 생성 프롬프트에 실은 사건의 근거라는 뜻이고, 엔진에 후보 재심사 단계가 없어 단계 축이 아직 한 겹이다 (ALPHA-603) | `(explanation_run_id, evidence_id, stage_code)` |
| `explanation_run_disclosure_fact` (+ `disclosure_fact`) | 번들 `evidences` 공시 갈래 lineage — 공시 정규화 사실이 어느 run 에 속하는지 + `disclosure_fact.document_id` 로 문서 도달. 조립 편입(ALPHA-718)으로 경계면이다 — "내부 구현·자유 변경" 아님, 양자 합의 대상 | `(explanation_run_id, fact_id, stage_code)` · `fact_id` |
| `document` (+ lineage `document_assertion`) | 번들 `evidences` = 근거 뉴스/공시 문서 목록 `{kind, title, source, published_at, source_uri}` — 온프렘 소비자(publication-api) 형상에 정렬 (ALPHA-395, source_uri 는 ALPHA-739 확장). document 로의 lineage: `run → …_event_evidence → event_evidence.assertion_id → document_assertion → document`, 양자 합의 | `document_id` |
| `event_thread` | 동일 실제 사건의 계보(후속 판정의 기준) | `thread_id` |
| `release_bundle` | 고객사가 승인·적용하는 제품 버전 manifest | `bundle_version` |
| `instrument` · `entity` | 번들의 `etf_ticker`·`etf_name` 공급(조인) — 온프렘 서빙 키가 ticker 라서 경계면에 포함 (확정 2026-07-21) | `instrument_id` = `entity_id` |

**게시 grain**: `explanation_result`의 결과 grain은 `(etf_instrument_id, trade_date, explanation_as_of)`이며, 이 grain에서 `publication_status = 'PUBLISHED'`인 행은 **부분 유니크 인덱스로 하나만 강제**된다(`uq_explanation_result_published_grain`). 재게시는 기존 게시본을 `WITHDRAWN`으로 내린 뒤 새 행을 게시한다. `DRAFT`·`WITHDRAWN` 이력은 같은 grain에 여러 건 남는다.

## ID 체계

- 도메인 ID(`explanation_result_id` 등)는 **Cloud 발번 TEXT** — 물리 스키마 기준. On-Prem 멱등 upsert 키 = 이 도메인 ID.
- `tenant_id`는 BIGINT(identity). 전달 단위의 멱등 처리 기준은 테넌트별 단조증가 cursor(ADR-0015 확정)이며, 저장 구조 차원의 키 설계는 전달 레코드 설계(미확정 — 아래)와 함께 확정한다.
- 무효화는 대상을 `target_explanation_result_id`로 참조한다 — 무효화의 단위는 특정 설명(리비전)이다([../domain/state-machine.md](../domain/state-machine.md), ADR-0044).

## 전달 레코드 (확정 — 2026-07-21, ALPHA-396)

번들은 테넌트별 전달 레코드를 cursor 순으로 묶어 생성한다 — cursor 발번 시점은 테넌트별 fan-out(ADR-0021 확정). 설계는 영서 단독 결정으로 다음과 같이 확정한다:

- **저장 구조**: `tenant_delivery` — 물리 정의는 `migrations-cloud/V202607211740__add_tenant_delivery.sql`(SQL이 계약). 컬럼: `(tenant_id, cursor)` PK · `delivery_type` · `explanation_result_id` · `target_explanation_result_id` · `reason` · `created_at`. 유형별 페이로드 형상(NEW=결과만 / INVALIDATION=대상+사유)은 CHECK 로 강제(2형상 축소는 `V202608011200`, ADR-0044) — 와이어 JSON 봉투와 1:1.
- **멱등 키**: 전달 단위는 `(tenant_id, cursor)` PK. On-Prem 소비 멱등은 별도 축 — 번들 단위는 `received_bundle.cursor_from`, 항목 단위는 도메인 ID(`explanation_result_id`) upsert.
- **페이로드는 저장하지 않는다**: 번들 조립 시점에 도메인 테이블(`explanation_result` 등)을 조인해 싣는다. 스냅샷 중복 저장을 피하는 walking skeleton 트레이드오프 — 전달 레코드 발번과 조립 사이에 결과가 바뀌면 조립 시점 상태가 실리며, 게시 철회는 다음 cursor(INVALIDATION)로 다시 전달되므로 수렴한다.
- **게시 상태 ↔ 전달 유형 매핑(fan-out 규칙)**: `explanation_result`가 `PUBLISHED`로 전이 → 대상 테넌트마다 `NEW` 1행 발번 / 게시 철회(`WITHDRAWN`) → `INVALIDATION`(사유 필수). cursor 는 테넌트별 단조증가로 발번기가 부여한다. (구 재게시→`CORRECTION` 매핑은 폐지 — ADR-0044.)
- **fan-out 발번기**: `NEW` 발번은 analysis-engine 이 `explanation_result` 게시와 **같은 트랜잭션**에서 수행한다(write-time fan-out, ALPHA-493 — 커밋된 행만 cursor 에 노출). `INVALIDATION` 발번은 super-admin-api 무효화 액션(`POST /api/v1/analyses/{id}/invalidate`, ALPHA-440)이 수행한다 — 게시본 `PUBLISHED→WITHDRAWN` 전이 + 발번 + 감사(admin_activity_log)가 **한 트랜잭션**이다. 발번 대상은 전 테넌트가 아니라 **그 결과의 NEW 를 받은 테넌트**다 — 원본 미수신 테넌트에 무효화를 발번하면 "원본 미수신 무효화 = gap 에서만 발생"(sync-protocol.md) 계약이 깨진다. 두 발번기는 같은 advisory lock(`hashtext('tenant-delivery-fanout')`)으로 cursor 채번을 직렬화한다 — 이 잠금 문자열은 두 모듈이 공유하는 계약이다. 발번 로직이 두 모듈에 상주할 뿐, 전달 레코드의 **소유는 ADR-0026 그대로 조영서**다 — fan-out 규칙·`tenant_delivery` 형상 변경은 이 계약 문서를 거친다. 같은 발화(explanation_route) 재실행분은 DRAFT 보존·발번 생략(발화당 첫 게시만 NEW — 무효화로 게시본이 사라진 발화는 재실행 시 새로 게시된다). **표면 부재 런은 첫 결과여도 DRAFT 로 적재하고 발번하지 않는다**(ALPHA-795) — 정적 설명 경로(`run_statics`)가 예외로 끝나 측정값 없이 "판정불가" 산문만 남은 런이다. ⚠️ 대표 사례는 층 분해 입력 결손(구성종목 이력·ETF 봉 부재)이지만 **판정은 예외 종류를 가리지 않는다**(`pipeline.py` 의 `except Exception`) — 요청창 수익률 미착지·레이크/Athena 조회 실패·구현 오류도 같은 경로로 DRAFT 가 된다. 그래서 "재게시가 안 됐다"의 진단은 데이터 백필이 아니라 `statics.surface.unavailable` 로그의 예외 타입에서 시작한다. ⚠️ **이 DRAFT 기제는 실시간·일일 런(`pipeline.run`) 것이다** — 요청창 백필(`window_batch`, `run_reason=WINDOW_BACKFILL`)은 종목별 실패 시 아무 행도 적재하지 않고 `window_batch.failed` 로만 남긴다(게시본 자리를 안 준다는 결과는 같지만 DRAFT 행이 없다). 그 경로의 부재를 DRAFT 로 찾으면 못 찾는다. "발화당 첫 결과가 그 발화의 최선"이 이 경우 깨져, 게시본 자리를 선점하면 데이터가 들어온 뒤의 재실행분이 DRAFT 로 밀린다(내용 없는 결과도 자리를 지킬 이유가 없다 — ADR-0045 의 무효화 게시본과 같은 결). 측정값이 있는 `UNCERTAIN`("원인 미확인")은 검수자가 판단할 값이 있으므로 그대로 게시·발번된다. 두 사유는 `explanation_result.publish_skipped` 로그의 `reason`(`route_already_published` / `surface_absent`)으로 갈린다. 하루 다건 발화(분봉 트리거)는 발화마다 게시·발번되며, 온프렘은 같은 (ticker, trade_date)의 스냅샷을 **교체 없이 공존 게시**하고 표시가 `explanation_as_of` 최신을 고른다(ADR-0045 결정 3 "유효 최신 승리", ALPHA-743 — 구 교체 규율(ALPHA-710)은 은퇴). **retention**: 미정(현재 무제한 보존) — 정리 정책은 운영 표준과 함께 후속.

`tenant_sync_cursor.last_cursor`(BIGINT)는 cursor 소비 추적이다 — 타입 정정은 `V202607150002`(근거: ADR-0015).

## Event Bundle JSON 구조

번들은 테넌트별 전달 레코드를 cursor 순으로 묶은 것이다. 엔트리 공통 봉투 + delivery_type별 페이로드:

```json
{
  "bundle_id": "0198...uuid",
  "tenant_id": 1,
  "generated_at": "2026-07-15T09:00:00Z",
  "cursor_from": 101,
  "cursor_to": 180,
  "entries": [
    {
      "cursor": 101,
      "delivery_type": "NEW",
      "explanation_result": { "explanation_result_id": "...", "etf_instrument_id": "...",
        "etf_ticker": "069500", "etf_name": "KODEX 200",
        "trade_date": "2026-07-15", "explanation_as_of": "...", "explanation_type": "EVENT_SUPPORTED",
        "summary": "...", "confidence_level": "MEDIUM", "primary_thread_id": "..." },
      "explanation_run": { "explanation_run_id": "...", "release_bundle_version": "..." },
      "source_events": [ { "source_event_id": "...", "source_class": "NEWS", "event_type_code": "EARNINGS", "event_date": "2026-07-14" } ],
      "evidences": [ { "kind": "NEWS", "title": "실적 발표 기사", "source": "YONHAP", "published_at": "2026-07-14T00:00:00Z", "source_uri": "https://news.example.com/a1" } ]
    },
    {
      "cursor": 103,
      "delivery_type": "INVALIDATION",
      "target_explanation_result_id": "...",
      "reason": "오탐지 이벤트"
    }
  ]
}
```

- **NEW는 전체 상태 전달(full snapshot)** — diff/patch가 아니다. On-Prem은 도메인 ID 기준 멱등 upsert만 하면 되고, 부분 갱신 병합 로직이 필요 없다.
- INVALIDATION 수신 시 On-Prem 동작(item·게시분 즉시 비노출)은 [../domain/state-machine.md](../domain/state-machine.md) 소관. 정정(CORRECTION) 형상은 계약에서 폐지됐다 — 소비자는 미지 유형과 동일하게 거부한다([ADR-0044](../adr/0044-correction-abolition.md)).

### `source_events`·`evidences` 경계면 컬럼 (확정 — 2026-07-24, ALPHA-395)

reader(영서) 단독 결정. 온프렘 검수 UI 요구(관련 뉴스/공시·근거 데이터·이벤트 타임라인 — [../console-ia/tenant-console.md](../console-ia/tenant-console.md))를 최소로 충족하는 컬럼만 싣는다(reader 자유·Rule 2).

- **`source_events[]`** ← `source_event` 4컬럼: `source_event_id`(식별) · `source_class`(NEWS/DISCLOSURE 분기) · `event_type_code`(변동 요인·타임라인 라벨) · `event_date`(타임라인 축). 제외: `available_at`·`lifecycle_stage`·`event_status`(내부 시각·상수 성격, UI 미요구). 이 4컬럼은 확정이다. **온프렘 소비자**: screening-worker 출처 수 정책 게이트(`SINGLE_SOURCE`·`min_source_count` — 고유 `source_event_id` 수를 센다)가 현재 소비자이고, 이벤트 타임라인 UI 는 추가 소비 예정이다(도입 시 형상 변경이 필요하면 확장-수축으로 처리 — 재협의 아님).
- **`evidences[]`** = 설명의 근거가 된 **뉴스/공시 문서 목록**. **실제 JSON 소비자인 `publication-api` `ExplanationStore`가 파싱하는 flat 형상**에 정렬한다(사용자 결정 2026-07-24 — Codex 지적으로 재정의): `kind`(NEWS/DISCLOSURE ← `document.document_type`) · `title`(헤드라인 ← `document.title`) · `source`(출처 ← `document.source_code`) · `published_at`(← `document.published_at`) · `source_uri`(원문 링크 ← `document.source_uri`, **optional** — ALPHA-739 확장, 콘솔 근거 제목 링크용. 구형 4키 저장분과 공존해야 하고 EOD 뉴스 채움 구멍(ALPHA-740)이 남아 required 승격은 구멍 해소 후). `event_evidence`의 내부 필드(`evidence_id`·`evidence_type`·`evidence_text`·`link_confidence`)는 소비자가 읽지 않아 싣지 않는다(Rule 2). **참고**: `tenant-console` 검수도 실전환됐다(ALPHA-607) — `analysis_item.evidences`(이 계약 형상 그대로 저장된 JSONB)를 파싱한다. 검수 심화용 필드가 필요해지면 확장-수축으로 추가.
- **lineage**: `evidences`(문서)는 두 갈래를 합쳐(distinct document) 도달한다 — ① `explanation_run → explanation_run_event_evidence → event_evidence.assertion_id → document_assertion.document_id → document`, ② `explanation_run → explanation_run_disclosure_fact → disclosure_fact.document_id → document`(공시 정규화 사실 — super-admin 콘솔 근거 표시와 같은 경로). `source_events`는 그 evidence의 `source_event_id`로 도달한다(distinct source_event). 조립 조인은 tenant-sync-api `TenantDeliveryRepository.findEvidenceRows`·`findSourceEventRows` 로 구현됐다(**ALPHA-718** — ALPHA-363 은 경계면 문서 편입으로 종결, 구현은 이 티켓). 기계가독 JSON Schema·양단 계약 테스트는 **ALPHA-497**. **두 배열 모두 조립돼 실린다 — lineage 없는 런은 빈 배열이다.**
- **변경 감지 대상(스키마 의존)**: 위 선별 컬럼은 물리 스키마에 의존하므로 변경 시 계약 영향 검토 대상이다 — `source_event(source_event_id, source_class, event_type_code, event_date)` · `document(document_type, title, source_code, published_at, source_uri)` · lineage 경로 `explanation_run_event_evidence` · `event_evidence(evidence_id, source_event_id, assertion_id)` · `document_assertion(assertion_id, document_id)` · 공시 갈래 `explanation_run_disclosure_fact(explanation_run_id, fact_id)` · `disclosure_fact(fact_id, document_id)`.
- **채움 보증 확인(진기, CODEOWNERS 리뷰)**: nullable 선별 컬럼은 `source_event.event_date`·`document.title`·`document.published_at`·`document.source_uri`다(`document.document_type`·`source_code`는 NOT NULL). `document.title`·`document.published_at`("관련 뉴스/공시" 제목·날짜)·`source_event.event_date`(타임라인 축)는 결정적 채움 보증 확인 대상. `document.source_uri` 는 실측 완료(2026-08-04, ALPHA-739): 공시=항상(DART 뷰어 URL 을 rcpNo 로 조립)·1분 뉴스=원천 URL 있으면 채움(canonical_news DO UPDATE — bigkinds `PROVIDER_LINK_PAGE` 결측 기사는 NULL)·**EOD 뉴스=조건부 결측**(assemble_events 선적재 시 NULL — 해소는 ALPHA-740). 어느 레인이든 NULL 이 가능하므로 계약은 nullable·optional 이다. 계약·구현(497)은 nullable 필드를 nullable로 모델링한다.

**형상** (두 배열 모두 조립 구현됨(ALPHA-718) — lineage 없는 런은 `[]`. 기계가독 스키마([event-bundle.schema.json](../../src/libs/schema/contracts/event-bundle.schema.json))는 요소 형상을 정의하되 `minItems: 0`이라 빈 배열·populated 둘 다 수용, ALPHA-497):

```json
"source_events": [ { "source_event_id": "...", "source_class": "DISCLOSURE", "event_type_code": "...", "event_date": "2026-07-14" } ],
"evidences": [ { "kind": "DISCLOSURE", "title": "삼성전자 공급계약 체결 공시", "source": "DART", "published_at": "2026-07-14T09:00:00Z" } ]
```

**`observed_return`·`market_code`(검수 UI 목록의 시장·등락률)**: 번들에 **싣기로 결정**하되, `source_events`/`evidences` 배열이 아니라 **`explanation_result` 페이로드 확장**이다 — `market_code`는 `instrument`(이미 `etf_ticker`·`etf_name` 조인) 출처, `observed_return`은 `price_movement_trigger` 출처. **결정적 lineage 조인 경로·기계가독 스키마화·openapi(ALPHA-326) 반영은 ALPHA-497로 이연**한다 — `price_movement_trigger`는 `(etf_instrument_id, trade_date, detected_at)` 유니크라 단순 (종목·거래일) 조인은 다중 트리거 시 비결정적이므로 조인 경로 확정이 497 몫이다. 여기선 "싣는다"는 결정만 기록한다.

## 무결성 (MVP: 전송 계층 · 목표 계약: 서명)

MVP의 앱 레벨 발신자 체크섬(`X-Bundle-Checksum`)·byte[] 응답은 폐기됐다([ADR-0040](../adr/0040-sync-integrity-mvp-to-signing.md)). 성공은 항상 200 공통 응답 포맷(`ApiResponse`)이다 — 번들은 `result` 아래, 신규 없음은 `result` 필드 생략([ADR-0042](../adr/0042-sync-pull-uniform-response.md) — 204 폐지).

- **MVP**: 전송 무결성은 mTLS/TLS(전송 계층)에 위임한다. Sync Agent는 수신 바이트를 재직렬화 없이 그대로 릴레이하고, Intake는 수신 body 원본을 Raw Event Store에 보존한다(수신 원본 불변 원칙).
- **목표 계약(서명)**: 종단 간 무결성·진정성이 필요해지면 벤더 개인키 기반 번들 서명을 도입한다 — "이 콘텐츠는 벤더가 발행한 원본"임을 증명. 서명 검증은 **수신 바이트 원본**을 대상으로 하며(같은 바이트 보존), canonical-JSON 정규화 규칙을 계약에 넣지 않기 위해 "받은 바이트 그대로"를 유지한다([sync-protocol.md](sync-protocol.md) 목표 계약).

## 미확정 요약

**영서 단독 결정**: 열린 항목 없음. ~~③ 번들에 실을 `source_events`·`evidences` 컬럼 선별~~ → 확정(위 "경계면 컬럼" 절, ALPHA-395) — 선별 컬럼은 변경 감지 대상으로 기록됨.

해소: ~~① 전달 레코드 설계 일체~~ → `tenant_delivery`로 확정(위 "전달 레코드" 절, ALPHA-396). retention 정리 정책만 후속. ~~② Tenant Sync API 엔드포인트 계약~~ → [sync-protocol.md](sync-protocol.md) "엔드포인트 계약(확정)" 절로 확정(ALPHA-397). ~~③ `source_events`·`evidences` 컬럼 선별~~ → 확정(ALPHA-395).

**진기 확인 대상**:
- (스키마) `tenant`·`tenant_credential` 정의 검토 — 인증 모델(sync-auth)과 함께 확정, 다르면 수축-확장으로 교체 ([ADR-0026](../adr/0026-ownership-boundary-db.md))
- (채움 보증, 미해소) 선별 nullable 컬럼 `source_event.event_date`·`document.title`·`document.published_at` 의 결정적 채움 보증 — 이 PR(ALPHA-395)의 CODEOWNERS 리뷰에서 진기 확인으로 해소 예정 (위 "경계면 컬럼" 절 참조)

해소된 안건: ~~confidence 스케일~~ → 물리 스키마의 `confidence_level` enum(HIGH/MEDIUM/LOW) 채택. ~~risk_grade 존치~~ → 물리 스키마에 없음. 산정 주체는 온프렘 Screening Worker 로 확정(2026-07-26 — Cloud AI 는 가드레일 제공만, 등급 기준은 증권사별 상이)이므로 번들 경계면에 risk_grade 는 싣지 않는 방향이 기본, 필요 시 확장-수축으로 추가. ~~ID 체계(UUIDv7 제안)~~ → 물리 스키마의 TEXT 도메인 ID 채택.
