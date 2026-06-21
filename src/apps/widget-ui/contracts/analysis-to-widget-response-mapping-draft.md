# Analysis → Widget Response Mapping Draft (S049)

## 1. 문서 목적

본 문서는 S049에서 분석 서버 v1 응답을 위젯 표준 응답으로 변환하는 Gateway adapter 매핑 규칙 초안을 정리한다.

- 확정 운영 스펙이 아니라 **adapter PoC**다.
- 실제 Gateway 서버/endpoint는 아직 확정되지 않았다.
- 실제 Public Embed Key 검증, 실제 ML API 호출, 실제 분석 DB 조회는 구현하지 않는다.
- adapter 로직은 `src/gateway-adapter.js`에 pure function으로 구현했으며, 실제 Gateway 레포가 생기면 그쪽으로 이동할 수 있도록 부수효과 없이 작성했다.

## 2. 분석 서버 v1 Response 구조

분석 서버 v1은 구조화된 factor/score 배열이 아니라 `affected_assets[].summary` 형태의 완성된 설명 문장을 제공한다.

```json
{
  "request_id": "req_20260312_005930_1d",
  "as_of": "2026-03-12T15:30:00+09:00",
  "affected_assets": [
    {
      "code": "005930.KS",
      "summary": "이번 삼성전자 하락은 반도체 규제 뉴스가 가장 크게 작용했어요. 전체 설명 중 절반 이상은 미국의 중국향 반도체 수출 규제 강화로 보는 게 자연스러워요."
    }
  ]
}
```

## 3. Gateway → Widget Response v1 구조

Gateway adapter가 반환하는 위젯 표준 응답 v1은 summary 중심 구조다. **아직 최종 확정 스펙은 아니며**, 실제 Gateway/분석 서버 연동 과정에서 변경될 수 있다.

```json
{
  "status": "success",
  "symbol": "005930",
  "generatedAt": "2026-03-12T15:30:00+09:00",
  "summary": "이번 삼성전자 하락은...",
  "cards": [
    {
      "title": null,
      "description": "이번 삼성전자 하락은..."
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

## 4. 매핑 규칙

| 위젯 응답 필드 | 출처 / 규칙 |
| --- | --- |
| `status` | adapter 판정 결과 (`success` / `empty` / `error` / `fallback`) |
| `symbol` | request `symbol`을 그대로 사용 (canonical 변환 안 함) |
| `generatedAt` | analysis `as_of`. 없으면 기본값 `2026-03-12T15:30:00+09:00` |
| `summary` | 매칭된 `affected_assets[].summary` |
| `cards[0].title` | analysis가 `title`을 제공하면 그대로 pass-through 매핑, 없으면 `null`(생략 가능). summary에서 생성/추론하지 않음 |
| `cards[0].description` | `summary`를 그대로 매핑 |
| `cards` | `summary`가 있으면 카드 1개, 없으면 `[]` |
| `disclaimer` | **Gateway adapter가 주입** (기본값 또는 `options.disclaimer`) |
| `newsLinks` | 현재 v1에서는 항상 빈 배열 |
| `fallback` | 기본 `{ isFallback: false, reason: null, basedAt: null }` |

adapter 함수:

- `mapAnalysisToWidgetResponse(analysisResponse, request, options)` — 변환 진입점
- `findMatchingAsset(affectedAssets, symbol)` — request symbol과 매칭되는 asset 탐색
- `normalizeSymbolForMatch(symbol)` — PoC용 단순 symbol 정규화
- `createSuccessResponse(asset, request, options)`
- `createEmptyResponse(request, options)`
- `createErrorResponse(request, error)`
- `createFallbackResponse(widgetResponse, reason, basedAt)`
- `buildSummaryCard(summary)`

`options`는 최소한 다음을 받는다.

```js
{
  disclaimer: "본 정보는 투자 참고용이며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다."
}
```

## 5. Symbol matching 정책

`normalizeSymbolForMatch(symbol)`는 PoC용 단순 매칭만 제공한다.

- trim
- `005930.KS` → `005930` (거래소 suffix 제거)
- `KRX:005930` → `005930` (vendor prefix 제거)
- 대소문자 차이는 무시
- 그 외 복잡한 vendor format은 처리하지 않는다.

> symbol canonicalization은 아직 확정되지 않았으며, S049에서는 PoC용 단순 매칭만 제공한다. 실제 운영에서는 Gateway adapter 또는 별도 symbol mapping layer에서 처리해야 한다.

가능한 입력 형식 (미정):

- 고객사 페이지: `005930`
- 분석 서버: `005930.KS`
- 일부 고객사: `KRX:005930`
- DB 저장 형식: FMP 형식 가능성 있으나 미정

## 6. empty / error / fallback 처리 기준

### success
- analysis response가 유효함
- `affected_assets`에 요청 symbol과 매칭되는 asset이 있음
- 매칭된 asset의 `summary`가 비어 있지 않음

### empty
현 단계에서는 "분석 결과 없음"으로 처리한다.

- `affected_assets`가 비어 있음
- 요청 symbol과 매칭되는 asset이 없음
- 매칭된 asset의 `summary`가 비어 있음

empty 응답의 `disclaimer`는 `"해당 종목의 최신 분석 결과가 없습니다."`다.

### error
- analysis response 자체가 `null`/`undefined`
- analysis response shape가 완전히 잘못됨 (`affected_assets`가 배열이 아님)
- adapter 내부에서 예외 발생

error 응답 예시:

```json
{
  "status": "error",
  "symbol": "005930",
  "message": "위젯 응답 변환 중 문제가 발생했습니다."
}
```

### fallback
- `createFallbackResponse(widgetResponse, reason, basedAt)`로 success/empty 응답을 fallback 상태로 감싼다.
- summary가 있으면 그대로 유지한다 (기존 S104 기준과 동일).
- 실제 cache/fallback 정책은 아직 구현하지 않는다.

## 7. 결정 사항

- **disclaimer는 Gateway adapter가 붙인다.** 분석 서버 응답에는 disclaimer가 없고, compliance 문구는 Gateway 책임으로 본다.
- **newsLinks는 현재 빈 배열이다.** 분석 서버 v1이 뉴스 링크를 제공하지 않으므로 v1에서는 항상 `[]`로 둔다.
- **organizationId / applicationId는 S049 adapter 출력에 포함하지 않는다.** S104 내부 mock은 `org_*`/`app_mts`를 붙였지만, 실제 tenant context 주입 방식이 미정이므로 S049 adapter v1 표준 응답에서는 제외한다 (추후 Gateway 구현 시 결정).
- **`cards[0].title`은 optional pass-through다.** 현재 분석 서버 v1은 `title`을 제공하지 않으므로 `cards[0].title`은 `null`이다. 분석 서버가 `title`을 제공하면 adapter가 해당 값을 그대로 `cards[0].title`로 매핑하고, 없으면 `null`로 둔다(생략 가능). adapter는 summary에서 title을 생성하거나 추론하지 않는다. `"가격 변동 설명"`은 adapter가 넣는 데이터가 아니라 위젯 UI가 title이 없을 때 표시하는 fallback label이며, 렌더링 계층(`renderSuccess`/`renderFallback`의 `card.title || '가격 변동 설명'`)에서 처리한다. 향후 분석 API가 title을 제공하면 adapter는 pass-through 매핑만 유지하면 되고 위젯 렌더링 구조는 크게 바꾸지 않는다.

## 8. Future Extension Fields

아래 필드는 현재 v1 응답에 포함하지 않으며, 향후 분석 모듈이 구조화된 factor/score를 제공할 때 확장 후보다.

- `impactDirection`
- `newsImpactScore`
- `abnormalReturn`

## 9. 아직 결정되지 않은 것 (미정 / 추후 Gateway 구현 시 결정)

- 실제 Gateway endpoint (URL, GET/POST, 인증 헤더 위치)
- 실제 tenant context 전달 방식 (embedKey 검증 결과를 downstream에 어떻게 전달할지)
- 실제 symbol canonicalization 정책 (종목 마스터 / mapping layer)
- 실제 newsLinks 제공 주체 (분석 서버 vs Gateway vs 별도 뉴스 서비스)
- 실제 compliance / disclaimer 정책 (지역/상품별 문구)

## 10. 다음 작업 연결

- **S046 (완료)**: local mock Gateway endpoint가 fixture 직접 조회 대신 `request → mock tenantContext → analysisApiClient.getLatestAnalysis() → mapAnalysisToWidgetResponse()` 흐름으로 동작하도록 분리했다(PoC). 상세: `Jira ALPHA-147`.
- **다음**: `widget.js`/Gateway가 실제 Gateway endpoint를 호출하도록 연결. `fetchLocalGateway()`의 endpoint를 실제 Gateway URL로 교체하고, 이 adapter·service 로직을 Gateway 서버로 이동한다. Public Embed Key 검증과 실제 tenantContext 생성도 그 시점에 확정한다.
