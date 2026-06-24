# widget-ui

> 역할/아키텍처는 루트 [README](../../../README.md)·[docs/architecture](../../../docs/architecture.md)가 SSOT.
> 이 문서는 로컬 실행·범위 경계만 둔다.
>
> 워킹 스켈레톤을 위젯 렌더링 책임으로 정리했다.
> 고객사 페이지엔 얇은 로더(`widget-loader.js`)만 들어가고, 실제 위젯(`widget.js`)은 iframe 본체(`widget-frame.html`)에서 부팅돼 gateway/mock 응답을 렌더링한다(라이브 경로는 `success`만, 4상태 렌더는 단위 테스트로 검증). iframe으로 고객사 페이지와 CSS가 격리된다.
> **gateway는 아직 mock** — 분석 API 호출 / adapter 변환 / Public Embed Key 검증은 후속 범위다(아래 "제외" 표).

## 실행

Node 패키지 매니저는 **pnpm**이다(ADR-0001). Node 워크스페이스 루트는 `src/pnpm-workspace.yaml`.

```bash
pnpm install                      # src/ (Node 루트)에서 1회
pnpm --filter widget-ui dev       # Vite dev 서버 → http://localhost:5173/test-client.html
pnpm --filter widget-ui test      # Vitest + jsdom (40 tests)
```

> pnpm이 없는 환경이면 `src/apps/widget-ui`에서 같은 바이너리를 `npx`로 실행한다: `npx vite`(dev), `npx vitest run --environment jsdom`(= `npm test`).

`test-client.html`은 고객사(증권사) 종목상세를 흉내 낸 데모다. 섹션 몇 개(차트/호가/뉴스 placeholder)와 "AI 분석" 섹션의 **단일 `<script>` 한 줄**(`widget-loader.js`)이 전부 — 고객사가 넣는 설치 코드가 한 줄임을 보여준다. AI 분석 섹션에서 `success` 상태를 확인한다. 상단 돋보기를 누르면 종목 검색 오버레이가 열리고, 데모 유니버스(미국 9종목)에서 종목을 고르면 헤더 종목명·코드가 바뀌며 그 `symbol`로 위젯이 재주입된다(**데모 한정** — 종목별 실제 분석 콘텐츠는 게이트웨이 연결 후, 현재 mock은 9종목 동일 스냅샷). `empty`/`error`/`fallback` 4상태 렌더는 PoC용 `data-mock-status`를 제거하면서 단위 테스트(`tests/widget.test.js`)로 검증한다.

## 핵심 경계 — loader / 본체(iframe) 2계층, widget = 렌더

- `widget-loader.js` — 고객사 페이지에 들어가는 얇은 단일 `<script>`. `data-embed-key`·`data-symbol`를 읽어 `widget-frame.html#key=&symbol=` iframe을 만들고, 본체가 `postMessage`로 알려온 높이로 iframe 높이를 맞춘다(모바일 스크롤바 제거). key·symbol은 쿼리스트링이 아니라 **URL fragment(`#…`)** 로 싣는다 — fragment는 HTTP 요청·Referer에 실리지 않아 종목/키가 정적 서버·CDN·프록시 로그에 남지 않는다.
- `widget.js` — iframe 본체에서 돈다. 입력은 iframe URL의 `#key=&symbol=`(`readConfig`)다. **변환을 하지 않고 widget response를 렌더링만** 한다. 흐름:

```
widget-loader.js (data-* → iframe #key=&symbol=, 높이 수신)
  └─ iframe: widget.js (URL #key=&symbol= 파싱 → request)
       → fetchMockGateway: 변환 끝난 widget response 스냅샷 반환 (서버 없이 오프라인, 라이브는 success)
       → renderWidget (success / empty / error / fallback) → 높이를 부모에 postMessage
```

> 라이브 부팅 경로는 항상 `success`를 렌더한다. `empty`/`error`/`fallback` 렌더 로직은 유지되며 렌더 단위 테스트로만 검증한다(`fetchMockGateway`의 status 인자는 그 테스트 용도).

analysis → widget 변환(adapter)은 Gateway 책임이며 **이 모듈 범위가 아니다** — 후속에서 구현한다.
iframe origin 엄격검증(`allowed_origins`/`frame-ancestors`)과 sandbox는 서버 트랙에서 도입한다 — 현재 로더는 `event.source` 동일성만 확인한다.

## 범위에서 의도적으로 제외한 것 (후속 티켓)

이 모듈은 위젯 렌더링 + mock 응답 표시까지다. Gateway/분석 체인은 한 모듈에 책임을 몰지 않도록 **이 범위에서 제외**했고, 후속에서 구현한다:

- analysis → widget 변환 adapter, local-api 모드, §5 응답 매핑
- Gateway 내부 흐름(tenantContext → 분석 API → adapter), Public Embed Key 검증

## 입력 계약 / 스파이크 노트

- [`notes/widget-data-attributes-note.md`](notes/widget-data-attributes-note.md) — **위젯 입력 계약(현 합의)**. 필수는 `data-embed-key`·`data-symbol` 둘뿐(키 1개 = 위젯 1개). **멀티테넌시 신뢰 기준은 embedKey**, 실질 방어선은 서버 `allowed_origins`. 키 모델 구조 자체는 변경 여지 일부 남음.
- [`notes/widget-gateway-note.md`](notes/widget-gateway-note.md) — Widget↔Gateway 요청/위젯 응답 v1 **스파이크 노트(미확정)**. adapter 매핑은 §6에서 후속으로 위임.

`data-widget-id`·`data-theme`·`data-client-id`는 계약에서 제거됐다 — 위젯 설정(테마 포함)은 키로 조회하는 서버(DB) 책임이다.
