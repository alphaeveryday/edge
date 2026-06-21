# Widget Gateway Contract Draft (S104 PoC)

## 1. 문서 목적

본 문서는 확정 API 스펙이 아닌 S104 PoC를 위한 Gateway 계약 초안이다.

- S104 PoC에서 `widget.js`가 단일 script 삽입으로 동작 가능한지 확인하기 위한 임시 요청/응답 규격이다.
- 실제 Gateway endpoint, 인증, ML API, 분석 DB, CDN 배포는 구현하지 않는다.
- 실제 운영 계약은 S016/S046/S049에서 별도 확정한다.

## 2. 구현 기준 정렬

- 위젯 스크립트: `widget.js`
- 실행 방식: Vanilla JavaScript IIFE
- 테스트 노출: `window.__EDGE_WIDGET_TEST_MODE__ = true`일 때만 `window.__EDGE_WIDGET_INTERNALS__` 노출
- 주요 흐름: `readConfig` → `validateConfig` → `createGatewayRequest` → `fetchMockGateway` → `renderWidget`
- mock 모드: `widget.js`는 변환하지 않고 이미 변환된 widget response 스냅샷을 status별로 그대로 렌더링한다(widget = 렌더). analysis → widget 변환(adapter)은 Gateway 책임이며 후속 S049에서 구현한다(§6).
- 렌더러: `renderSuccess`, `renderEmpty`, `renderError`, `renderFallback`
- 지원 mock 상태: `success`, `empty`, `error`, `fallback`
- 스타일 주입: `id="edge-widget-style"`로 `document.head`에 1회 삽입
- S016 data attribute 계약: `widget-data-attributes-contract.md`

## 3. Widget → Gateway Request Draft

### 3.1. 요청 JSON 예시

```json
{
  "embedKey": "pub_demo_1234",
  "clientId": "demo-sec",
  "widgetId": "asset-event-impact",
  "symbol": "005930",
  "theme": "default"
}
```

### 3.2. 필드 의미

| 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `embedKey` | string | 필수 | Public Embed Key. 실제 tenant 식별은 이 값의 Gateway 검증 결과를 기준으로 한다 |
| `widgetId` | string | 필수 | 렌더링할 위젯 타입 식별자. `data-widget-id` 기반 |
| `symbol` | string | 필수 | 대상 종목 코드. 위젯은 trim 후 non-empty만 검증하고 canonical 변환은 adapter에 위임 |
| `clientId` | string | 아니오 | 고객사 식별 보조값. 값이 있을 때만 request에 포함하며 신뢰 가능한 tenant 식별자가 아니다 |
| `theme` | string | 아니오 | 테마 식별자. 현재 공식 지원값은 `default`이며 unknown 값도 `default`로 fallback |

`data-mock-status`는 요청 바디에 포함하지 않는다. S104/S016 PoC 테스트 분기 제어용 속성이며 실제 Gateway 계약이 아니다.

## 4. Analysis Server Response v1

분석 서버 v1 응답은 `affected_assets[].summary` 형태의 완성된 설명 문장을 제공하는 방식으로 고정한다.

```json
{
  "request_id": "req_20260312_005930_1d",
  "as_of": "2026-03-12T15:30:00+09:00",
  "affected_assets": [
    {
      "code": "005930.KS",
      "summary": "이번 삼성전자 하락은 반도체 규제 뉴스가 가장 크게 작용했어요..."
    }
  ]
}
```

현재 v1에서는 구조화된 factor/score 배열을 필수 응답으로 보지 않는다.

## 5. Gateway → Widget Response v1

Gateway v1은 분석 서버 응답을 위젯 응답으로 감싸서 반환하는 adapter 역할을 맡는다.

> `organizationId`/`applicationId`/`widgetId`는 **표준 응답에 포함하지 않는다.** 이전 mock은 `org_*`/`app_mts`를 붙였지만, 실제 tenant context 주입 방식이 미정이라 위젯 응답 표준에서 제외하기로 결정했다(충돌 평균 금지). tenant context 주입은 후속 S046에서 확정한다.

### 5.1. `status: "success"` 예시

```json
{
  "status": "success",
  "symbol": "005930",
  "generatedAt": "2026-03-12T15:30:00+09:00",
  "summary": "이번 삼성전자 하락은 반도체 규제 뉴스가 가장 크게 작용했어요...",
  "cards": [
    {
      "title": null,
      "description": "이번 삼성전자 하락은 반도체 규제 뉴스가 가장 크게 작용했어요..."
    }
  ],
  "disclaimer": "본 정보는 투자 참고용이며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.",
  "newsLinks": [],
  "fallback": {
    "isFallback": false,
    "reason": null,
    "basedAt": null
  }
}
```

### 5.2. 필드 의미

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `status` | string | 위젯 분기 기준 값 (`success`/`empty`/`error`/`fallback`) |
| `symbol` | string | 대상 종목 코드 |
| `generatedAt` | string | 분석 기준 시각. S104에서는 analysis `as_of`를 사용 |
| `summary` | string | 위젯 상단 요약. analysis `affected_assets[].summary`에서 매핑 |
| `cards` | array | v1에서는 대표 카드 1개만 사용 |
| `cards[0].title` | string·null | optional pass-through. analysis가 `title`을 제공하면 매핑, 없으면 `null`. `"가격 변동 설명"`은 위젯 UI fallback label (S049) |
| `cards[0].description` | string | analysis summary를 그대로 매핑 |
| `disclaimer` | string | 위젯 하단 공지 문구 |
| `newsLinks` | array | 관련 뉴스 링크. 현재 mock은 빈 배열 |
| `fallback` | object | fallback 여부/사유/기준시각 |
| `message` | string | `status: error`에서 표시하는 오류 메시지 |

## 6. adapter / Gateway 내부 흐름 — 후속 티켓 (S046 · S049)

이 스파이크(ALPHA-263) 범위는 **widget = 렌더**까지다. `widget.js`는 변환하지 않고, 이미 변환이 끝난 widget response 스냅샷(`MOCK_WIDGET_RESPONSE_SUCCESS` / `_EMPTY` / `_ERROR` + fallback 합성)을 status별로 렌더링만 한다(`fetchMockGateway`, 서버 없이 도는 오프라인 대역).

아래 **Gateway 변환·내부 흐름은 의도적으로 제외**했고 후속 티켓에서 구현한다:

- **S049 (ALPHA-150)** — analysis → widget 변환 adapter(`mapAnalysisToWidgetResponse`, symbol 매칭, disclaimer 주입 등)와 §5 응답 매핑 규칙. local-api 모드도 여기서.
- **S046 (ALPHA-147)** — Gateway 내부 흐름(`request → tenantContext → 분석 API 호출 → adapter`), Public Embed Key 검증, tenant context 생성.

개념: **widget = 렌더, Gateway = 변환.** 실제 Gateway 연결 시 widget의 렌더 계약(§5)은 그대로 두고 변환을 Gateway가 맡는다.

## 7. 상태별 응답

- `success`: analysis summary를 위젯 응답으로 변환해 렌더링
- `empty`: `summary: ""`, `cards: []`, 데이터 없음 메시지 렌더링
- `error`: 오류 메시지 렌더링
- `fallback`: success widget response에 `fallback.isFallback: true`, `fallback.reason`, `fallback.basedAt`을 추가해 렌더링

## 8. Future Extension Fields

아래 필드는 현재 v1 mock 응답에 포함하지 않는다.

- `impactDirection`
- `newsImpactScore`
- `abnormalReturn`

이 필드들은 향후 분석 모듈이 구조화된 factor/score/abnormalReturn을 제공할 때 확장 후보로 재검토한다. 현재 S104 v1 구현과 테스트는 이 필드들을 필수값으로 다루지 않는다.

## 9. 아직 실제 구현이 아닌 것

- 실제 Gateway endpoint
- 실제 Public Embed Key 검증
- 실제 Organization/Application 식별
- 실제 ML API 호출
- 실제 분석 DB 조회
- 실제 운영용 CDN 배포

## 10. 다음 작업 연결

- S016: data attribute/request 계약 정리 (`widget-data-attributes-contract.md`)
- **S049 (ALPHA-150)**: analysis 응답을 위젯 표준 응답으로 변환하는 Gateway adapter + §5 매핑 규칙 구현.
- **S046 (ALPHA-147)**: Gateway 내부 흐름(`request → tenantContext → 분석 API → adapter`), Public Embed Key 검증, tenant context 생성.
