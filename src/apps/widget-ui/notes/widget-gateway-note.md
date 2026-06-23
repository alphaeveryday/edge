# Widget ↔ Gateway 요청/응답 — 스파이크 노트

> 이 문서는 **계약 SSOT가 아니다.** 스파이크에서 도출한 미확정 노트이며, 확정되면 ADR/`schema`로 증류해 `docs/`에 등록한다.

## 1. 목적

확정 API 스펙이 아닌 스파이크용 Widget↔Gateway 요청/응답 초안 노트다.

- 단일 `<script>`(`widget-loader.js`) 삽입 → iframe(`widget-frame.html`) 본체(`widget.js`)로 동작 가능한지 확인하기 위한 임시 요청/응답 규격이다.
- 실제 Gateway endpoint, 인증, ML API, 분석 DB, CDN 배포는 구현하지 않는다.
- 실제 운영 규격은 후속에서 별도 확정한다.

## 2. 구현 기준 정렬

- 위젯 스크립트: 고객사 진입점 `widget-loader.js`(얇은 로더) + 본체 `widget.js`(iframe `widget-frame.html`에서 실행)
- 실행 방식: Vanilla JavaScript IIFE
- 테스트 노출: `window.__EDGE_WIDGET_TEST_MODE__ = true`일 때만 `window.__EDGE_WIDGET_INTERNALS__` 노출
- 주요 흐름: `readConfig`(iframe URL `#key=&symbol=`) → `validateConfig` → `createGatewayRequest` → `fetchMockGateway` → `renderWidget`
- mock 모드: `widget.js`는 변환하지 않고 이미 변환된 widget response 스냅샷을 렌더링한다(widget = 렌더). 라이브 부팅 경로는 `success`만 렌더하며, analysis → widget 변환(adapter)은 Gateway 책임이라 후속에서 구현한다(§6).
- 렌더러: `renderSuccess`, `renderEmpty`, `renderError`, `renderFallback` (4상태 렌더는 단위 테스트로 검증)
- 스타일: 위젯 CSS는 본체 프레임 `widget-frame.html`의 정적 스타일(이전 `injectStyle` head 주입은 iframe 격리로 제거)
- data attribute 계약: `widget-data-attributes-note.md`

## 3. Widget → Gateway Request Draft

> **request 입력 계약 SSOT는 `widget-data-attributes-note.md` §8이다.** 이 계약에서 `embedKey + symbol`로 축소했다(키 1개 = 위젯 1개, 위젯 설정은 키로 서버에서 조회). 이전 초안의 `widgetId`/`theme`/`clientId`는 제거됐다.

### 3.1. 요청 JSON 예시

```json
{
  "embedKey": "pub_demo_1234",
  "symbol": "005930"
}
```

### 3.2. 필드 의미

| 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `embedKey` | string | 필수 | Public Embed Key. 실제 tenant·위젯 식별은 이 값의 Gateway 검증 결과를 기준으로 한다 |
| `symbol` | string | 필수 | 대상 종목 코드. 위젯은 trim 후 non-empty만 검증하고 canonical 변환은 adapter에 위임 |

PoC용 `data-mock-status`는 계약·구현에서 제거됐다(입력 계약 노트 §7). 요청 바디는 `embedKey`/`symbol`만 담는다.

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

> `organizationId`/`applicationId`/`widgetId`는 **표준 응답에 포함하지 않는다.** 이전 mock은 `org_*`/`app_mts`를 붙였지만, 실제 tenant context 주입 방식이 미정이라 위젯 응답 표준에서 제외하기로 결정했다(충돌 평균 금지). tenant context 주입은 후속에서 확정한다.

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
| `generatedAt` | string | 분석 기준 시각. 현재는 analysis `as_of`를 사용 |
| `summary` | string | 위젯 상단 요약. analysis `affected_assets[].summary`에서 매핑 |
| `cards` | array | v1에서는 대표 카드 1개만 사용 |
| `cards[0].title` | string·null | optional pass-through. analysis가 `title`을 제공하면 매핑, 없으면 `null`. `"가격 변동 설명"`은 위젯 UI fallback label |
| `cards[0].description` | string | analysis summary를 그대로 매핑 |
| `disclaimer` | string | 위젯 하단 공지 문구 |
| `newsLinks` | array | 관련 뉴스 링크. 현재 mock은 빈 배열 |
| `fallback` | object | fallback 여부/사유/기준시각 |
| `message` | string | `status: error`에서 표시하는 오류 메시지 |

## 6. adapter / Gateway 내부 흐름 — 후속 티켓

이 스파이크 범위는 **widget = 렌더**까지다. `widget.js`는 변환하지 않고, 이미 변환이 끝난 widget response 스냅샷(`MOCK_WIDGET_RESPONSE_SUCCESS` / `_EMPTY` / `_ERROR` + fallback 합성)을 status별로 렌더링만 한다(`fetchMockGateway`, 서버 없이 도는 오프라인 대역).

아래 **Gateway 변환·내부 흐름은 의도적으로 제외**했고 후속 티켓에서 구현한다:

- analysis → widget 변환 adapter(`mapAnalysisToWidgetResponse`, symbol 매칭, disclaimer 주입 등)와 §5 응답 매핑 규칙. local-api 모드도 여기서.
- Gateway 내부 흐름(`request → tenantContext → 분석 API 호출 → adapter`), Public Embed Key 검증, tenant context 생성.

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

이 필드들은 향후 분석 모듈이 구조화된 factor/score/abnormalReturn을 제공할 때 확장 후보로 재검토한다. 현재 v1 구현과 테스트는 이 필드들을 필수값으로 다루지 않는다.

## 9. 아직 실제 구현이 아닌 것

- 실제 Gateway endpoint
- 실제 Public Embed Key 검증
- 실제 Organization/Application 식별
- 실제 ML API 호출
- 실제 분석 DB 조회
- 실제 운영용 CDN 배포

## 10. 다음 작업 연결

- data attribute/request 계약 정리 (`widget-data-attributes-note.md`)
- analysis 응답을 위젯 표준 응답으로 변환하는 Gateway adapter + §5 매핑 규칙 구현.
- Gateway 내부 흐름(`request → tenantContext → 분석 API → adapter`), Public Embed Key 검증, tenant context 생성.
