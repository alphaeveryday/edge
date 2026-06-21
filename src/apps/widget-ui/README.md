# widget-ui — ALPHA-263 워킹 스켈레톤 (스파이크)

> **스파이크다. 운영 구현이 아니다.** 실제 Gateway/분석 API/Public Embed Key 검증/CDN 배포는 없다.
> mock으로 얕은 E2E를 한 번 통과시켜 동작 플로우와 계약 초안을 도출한 M1 기록이다.
> 모듈 역할(위젯 임베드 프론트엔드)은 루트 [README.md](../../../README.md)가 SSOT — 여기선 로컬 실행/특이점만 둔다.

## 실행

Node 패키지 매니저는 **pnpm**이다(ADR-0001). Node 워크스페이스 루트는 `src/pnpm-workspace.yaml`.

```bash
pnpm install                      # src/ (Node 루트)에서 1회
pnpm --filter widget-ui dev       # Vite dev 서버 → http://localhost:5173/test-client.html
pnpm --filter widget-ui test      # Vitest + jsdom (71 tests)
```

`test-client.html`은 고객사 MTS 종목상세에 위젯을 삽입한 데모다. `success`는 AI분석 탭 본문에서, `empty`/`error`/`fallback`은 하단 "개발자 검증 정보 보기" 패널의 `data-mock-status` 위젯에서 확인한다.

## 핵심 경계 — widget = 렌더, gateway = 변환

`widget.js`는 단일 `<script>` 로더이며 **변환을 하지 않고 widget response를 렌더링만** 한다.
analysis → widget 변환(adapter)은 gateway 책임(`src/gateway-adapter.js`)이다. 흐름:

```
widget.js (data-* 파싱 → request)
  → [mock 모드]  변환 끝난 widget response 스냅샷을 status별로 반환 (서버 없이 오프라인)
  → [local-api]  POST /mock-gateway/widget-analysis (vite.config.js dev 미들웨어)
                   → createMockTenantContext → analysisApiClient.getLatestAnalysis
                   → mapAnalysisToWidgetResponse(adapter) → widget response
  → renderWidget (success / empty / error / fallback)
```

mock 스냅샷이 adapter 실제 출력과 어긋나지 않는지는 `tests/widget.test.js`의 일관성 테스트가 보장한다.

## 스파이크 경계 (M2/M3로 이동 예정)

이 모듈에 **mock**으로 같이 둔 아래 코드는 본래 다른 서비스 책임이다. 실제 구현은 후속 스토리에서 해당 서비스로 옮긴다:

| 파일 | 실제 소속 | 후속 스토리 |
|---|---|---|
| `src/gateway-adapter.js`, `src/mock-gateway-service.js`, `src/mock-tenant-context.js` | `gateway` | S049(ALPHA-150), S046(ALPHA-147) |
| `src/analysis-api-client.js`, `src/fixtures/` | `widget-api` / 분석 | — |

`widget.js`/`test-client.html`만 widget-ui에 잔류한다 (S104 ALPHA-205는 M2/M3).

## 계약 초안

다음 스프린트 합의 대상. 확정 시 ADR/`schema`로 증류해 [docs 지도](../../../docs/README.md)에 등록한다.

- [`contracts/widget-data-attributes-contract.md`](contracts/widget-data-attributes-contract.md) — `data-*` 계약(S016). 필수 `data-embed-key`·`data-widget-id`·`data-symbol`. **멀티테넌시 신뢰 기준은 embedKey**(clientId 아님).
- [`contracts/widget-gateway-contract-draft.md`](contracts/widget-gateway-contract-draft.md) — Widget↔Gateway 요청/응답 v1(S104).
- [`contracts/analysis-to-widget-response-mapping-draft.md`](contracts/analysis-to-widget-response-mapping-draft.md) — 분석 v1 → 위젯 응답 adapter 매핑(S049).

`data-theme`는 현재 `default`만 지원한다(unknown 값도 `default`로 fallback). light/dark는 추후 확장.
