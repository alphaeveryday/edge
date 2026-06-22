# widget-ui

> 역할/아키텍처는 루트 [README](../../../README.md)·[docs/architecture](../../../docs/architecture.md)가 SSOT.
> 이 문서는 로컬 실행·범위 경계만 둔다.
>
> ALPHA-263 스파이크의 워킹 스켈레톤을 위젯 렌더링 책임으로 정리했다.
> 단일 `<script>` 로더가 고객사 페이지 삽입을 가정한 데모에서 부팅돼 gateway/mock 응답을 4상태로 렌더링한다.
> **gateway는 아직 mock** — 분석 API 호출 / adapter 변환 / Public Embed Key 검증은 후속 범위다(아래 "제외" 표).

## 실행

Node 패키지 매니저는 **pnpm**이다(ADR-0001). Node 워크스페이스 루트는 `src/pnpm-workspace.yaml`.

```bash
pnpm install                      # src/ (Node 루트)에서 1회
pnpm --filter widget-ui dev       # Vite dev 서버 → http://localhost:5173/test-client.html
pnpm --filter widget-ui test      # Vitest + jsdom (26 tests)
```

> pnpm이 없는 환경이면 `src/apps/widget-ui`에서 같은 바이너리를 `npx`로 실행한다: `npx vite`(dev), `npx vitest run --environment jsdom`(= `npm test`).

`test-client.html`은 고객사 MTS 종목상세에 위젯을 삽입한 데모다. `success`는 AI분석 탭 본문에서, `empty`/`error`/`fallback`은 하단 "개발자 검증 정보 보기" 패널의 `data-mock-status` 위젯에서 확인한다.

## 핵심 경계 — widget = 렌더

`widget.js`는 단일 `<script>` 로더이며 **변환을 하지 않고 widget response를 렌더링만** 한다. 흐름:

```
widget.js (data-* 파싱 → request)
  → fetchMockGateway: 변환 끝난 widget response 스냅샷을 status별로 반환 (서버 없이 오프라인)
  → renderWidget (success / empty / error / fallback)
```

analysis → widget 변환(adapter)은 Gateway 책임이며 **이 모듈 범위가 아니다** — 후속 S049에서 구현한다.

## 범위에서 의도적으로 제외한 것 (후속 티켓)

이 모듈은 위젯 렌더링 + mock 응답 표시까지다. Gateway/분석 체인은 한 모듈에 책임을 몰지 않도록 **이 범위에서 제외**했고, 티켓 명시대로 후속에서 구현한다:

| 제외 대상 | 후속 티켓 |
|---|---|
| analysis → widget 변환 adapter, local-api 모드, §5 응답 매핑 | **S049 (ALPHA-150)** |
| Gateway 내부 흐름(tenantContext → 분석 API → adapter), Public Embed Key 검증 | **S046 (ALPHA-147)** |

## 스파이크 노트 (계약 후보 · 미확정)

아래는 **확정 계약이 아니라** 스파이크에서 도출한 탐색 노트다. 계약 SSOT가 아니므로
[docs/](../../../docs/README.md)에 두지 않고 스파이크와 함께 둔다. 다음 스프린트에서 합의·확정되면
그때 ADR/`schema`로 증류해 docs 지도에 등록한다(설계·계약 SSOT는 docs).

- [`notes/widget-data-attributes-note.md`](notes/widget-data-attributes-note.md) — `data-*` 노트(S016). 필수 `data-embed-key`·`data-widget-id`·`data-symbol`. **멀티테넌시 신뢰 기준은 embedKey**(clientId 아님).
- [`notes/widget-gateway-note.md`](notes/widget-gateway-note.md) — Widget↔Gateway 요청/위젯 응답 v1 노트(S104). adapter 매핑은 §6에서 S049로 위임.

`data-theme`는 현재 `default`만 지원한다(unknown 값도 `default`로 fallback). light/dark는 추후 확장.
