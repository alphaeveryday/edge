# MTS/HTS 연동 방식 — Publication API

> 이 문서는 증권사 백엔드 개발자에게 전달되는 연동 기준의 원본이다(대외 산출물 — 표현은 [../writing-rules.md](../writing-rules.md) 준수).
>
> **문법 명세(기계가독)** — 경로·필드·타입의 기계가독 명세는 [publication-api/openapi.yaml](../../src/apps/onprem/publication-api/openapi.yaml)(OpenAPI 3.1, ALPHA-498). 이 문서(시맨틱 계약)가 상위 SSOT이고 openapi 는 하위 문법 층이다(TODO §5 2층 구조) — 의미가 충돌하면 이 문서가 이긴다.

## 원칙 (확정)

- MTS/HTS는 증권사 소유 UI다. 벤더가 **서빙·호스팅하는** widget 런타임은 임베드하지 않는다 — EDGE 위젯 UI는 빌드 산출물로 납품되어 증권사가 자기 환경에서 임베드·호스팅한다([ADR-0035](../adr/0035-widget-ui-build-artifact.md)). 어느 경우든 데이터는 증권사 백엔드/API GW 경유로만 Publication API에 도달하고, 위젯이 벤더 클라우드를 직접 호출하지 않는다.
- 경로: **MTS/HTS → 증권사 Backend/API Gateway → On-Premise Publication API**. MTS가 Publication API를 직접 호출하지 않는다.
- Publication API는 **Published(AUTO_PUBLISHED, APPROVED) 상태만 반환**. 검수 대기/차단/반려/무효화/노출중단 상태는 절대 반환하지 않는다 — 응답에 존재할 수 없는 것이 제품 보장이다.
- Publication API 호출 시 증권사 백엔드가 **고객 식별 해시**를 전달 → 조회 시점에 Exposure Log 자동 기록(조회=노출 간주, [../domain/exposure-log.md](../domain/exposure-log.md)). 원본 고객 ID/계좌번호는 절대 받지 않는다.
- Publication API 자체의 인증은 증권사 내부망 정책(내부 API GW)에 위임한다. 벤더 API Key 없음.

## 엔드포인트 스펙 (초안 — ALPHA-366)

> `[확정 필요]` 표기 항목 외에는 기확정 결정에서 도출된 제안값이다. 데모 페이지(가상 MTS)는 이 스펙 기준으로 mock을 만든다.

### 조회 — ETF 가격 변동 설명 (단건)

```
GET /api/v1/explanations/{etf_ticker}?trade_date={yyyy-MM-dd}
X-Customer-Hash: <증권사 백엔드가 생성한 고객 식별 해시>
X-Channel: MTS | HTS | INTERNAL
```

- `etf_ticker`: 국내 상장 ETF 종목코드 (MVP 커버리지 — [../adr/0024](../adr/0024-scope-domestic-etf.md)). `[확정 필요 — 식별자 체계: 종목코드 vs ISIN. 초안은 종목코드]`
- `trade_date` 생략 시 **최신 거래일**의 게시분 — 화면(AI 분석 탭)은 "가장 최근 거래일의 분석"을 원하므로 게시 시각이 아니라 거래일 기준이다. 과거 거래일 분석이 나중에 게시(지연 검수)돼도 최신 거래일 게시분이 우선한다. 같은 거래일에 유효 스냅샷이 여럿 공존하면 `explanation_as_of` 최신이 이긴다(무효화분 제외 — "유효 최신 승리", ADR-0045 결정 3·ALPHA-743). 최신이 무효화되면 직전 유효 스냅샷이 이 규칙만으로 자동 재노출된다. 응답의 `explanation_as_of`가 스냅샷 기준시각을 말한다. 동률은 게시 시각으로 해소.
- `X-Customer-Hash` 필수 — 해시 생성 규칙·salt는 증권사 관리 영역(벤더 불관여). 누락 시 400.
- `X-Channel` 필수 — Exposure Log의 채널 필드.

**응답 200** (노출 가능한 설명이 있을 때):

```json
{
  "publication_id": "...",
  "etf": { "ticker": "069500", "name": "KODEX 200" },
  "trade_date": "2026-07-15",
  "summary": "반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
  "confidence_level": "MEDIUM",
  "evidences": [
    { "kind": "NEWS", "title": "반도체 수출 반등", "source": "...", "published_at": "..." }
  ],
  "disclaimer": "본 내용은 공개 정보 기반의 변동 요인 후보이며 투자 권유가 아닙니다.",
  "published_at": "2026-07-15T16:40:00+09:00"
}
```

- `summary`는 검수를 거친 **최종 노출 문구**다(원본 AI 문구가 아니라 검수자 수정 반영본). 원천은 Cloud `explanation_result.summary`(물리 스키마의 유일한 고객 노출 텍스트 필드) — 번들로 온프렘에 수신된 뒤 검수를 거친 값이다.
- `disclaimer`는 테넌트 정책의 기본 안내 문구 — 화면에 반드시 함께 노출한다.
- `evidences` 요소 형상은 `{kind, title, source, published_at}`(근거 뉴스/공시 문서 목록)로 **확정**(ALPHA-395 — [event-bundle-schema.md](event-bundle-schema.md) "경계면 컬럼" 절). 번들 `evidences`가 온프렘 저장(`analysis_item.evidences`)을 거쳐 이 응답으로 서빙된다(`ExplanationStore`가 파싱하는 형상 — 저장분에는 `source_uri`(ALPHA-739, 검수 콘솔용)도 있으나 **서빙 계약에는 싣지 않는다**: 내부 lineage URI 를 고객 표면에 노출하지 않음). 반대 요인 등 부가 텍스트는 물리 스키마에 전용 컬럼이 없어(candidate: `stage_results` JSONB) 계약에 넣지 않는다 — 필요해지면 스키마 확장(양자 합의) 후 추가.
- **이 200 응답이 Exposure Log 기록 시점**이다 — 응답한 문구 스냅샷·고객 해시·채널·시각이 기록되어 민원·감사 시 재현된다.

**응답 204** (해당 ETF·일자에 노출 가능한 설명이 없을 때): 정상 상태다 — 모든 ETF가 매일 설명을 갖지 않는다. body 없음, Exposure Log 기록 없음.

**에러**:

| 코드 | 의미 |
|---|---|
| 400 | `X-Customer-Hash`/`X-Channel` 누락, 잘못된 `trade_date` 형식 |
| 404 | 알 수 없는 ETF 종목코드 |
| 5xx | 서버 오류 — 증권사 백엔드는 폴백 문구 처리 권장(설명 미제공이 고객 화면 오류로 보이지 않게) |

- 에러 body 형상은 jvm-common 공통 응답 포맷 `ApiResponse` — `{ "isSuccess": false, "code": "SERV4001", "message": "..." }`(result 생략). 도메인 코드 `SERV4001`~`SERV4004`(400)·`SERV4040`(404), 프레임워크 예외는 상태코드 기반 공통 코드(`COMMON404` 등). 성공(200) 본문은 이 포맷으로 감싸지 않는다. (확정 — ALPHA-498, 코드 `PublicationErrorStatus`·openapi `ErrorResponse`)

### 미확정 목록

1. 식별자 체계 (종목코드 vs ISIN)
2. 목록/배치 조회(여러 ETF 한 번에) 필요 여부 — MTS 관심목록 화면이 요구하면 추가
3. 캐싱 지시(ETag 등) — 조회=노출 시맨틱과의 상호작용 검토 필요 (`[주의]` 증권사 백엔드가 응답을 캐시하면 Exposure Log가 실노출보다 적게 기록된다 — 연동 가이드에 캐시 금지 또는 노출 콜백(로드맵, [../roadmap.md](../roadmap.md)) 안내)
