# super-admin-ui

> 역할/아키텍처는 루트 [README](../../../../README.md)·[docs/context.md](../../../../docs/context.md)가 SSOT.
> 이 문서는 로컬 실행·범위 경계만 둔다.

운영자 콘솔(EDGE Admin). tenant-console-ui 와 동일 스택 — Vite + React 19 + TypeScript +
Tailwind CSS v4 + TanStack Query, 디자인 시스템은 [`ui-kit`](../../../libs/ui-kit) 공유.
화면은 claude.ai/design 시안 **v0.1** (EDGE Wireframe Design System) 기준.

## 실행

Node 패키지 매니저는 **pnpm**이다(ADR-0001). Node 워크스페이스 루트는 `src/pnpm-workspace.yaml`.

```bash
# 백엔드 먼저 (레포 루트에서) — admin API 는 fail-closed 라 API 없이는 화면 데이터가 비어 있다
docker compose up --build super-admin-api      # host 18082

pnpm --filter super-admin-ui dev        # http://localhost:5175
pnpm --filter super-admin-ui build      # tsc --noEmit && vite build → dist/
pnpm --filter super-admin-ui typecheck
pnpm --filter super-admin-ui test       # node:test — 규칙 엔진 + 도메인 판단 모듈 단위 테스트
pnpm --filter super-admin-ui eval:rules # 규칙 평가 결과 JSON (UI 없이)
```

⚠️ `test`·`eval:rules` 는 **Node 23.6+** 가 필요하다 — `.ts` 를 네이티브로 실행한다(레포에
버전 핀이 없다). 배포 워크플로는 Node 20 이라 못 돌고, PR 게이트
[`ui-test.yml`](../../../../.github/workflows/ui-test.yml)이 Node 24 로 돌린다. 그 워크플로가
UI 의 **유일한 PR 체크**다 — 그전에는 타입 오류가 PR 이 아니라 dev 배포를 깨뜨렸다.

dev 서버는 `/api` 를 super-admin-api(기본 `http://localhost:18082`, bootRun 직접
기동이면 `VITE_API_PROXY_TARGET=http://localhost:8080`)로 프록시한다 — same-origin 이
되어 세션 쿠키(SameSite=Strict)가 실린다. 진입은 로그인 화면(`/login`, ALPHA-616)
경유가 유일한 경로다 — 미인증·만료는 `RequireSession` 가드가 `/login` 으로 보내고,
로그인 후 원래 경로로 복귀한다(구 devSession 자동 로그인은 제거됨). 로컬 계정은
`operator@edge.local` / `demo-operator-1`(compose 가 주입 — docker-compose.yml).
정적 배포본(S3/CloudFront, `admin-dev.edgesignal.dev`)은 CloudFront 가 `/api/*` 를
admin ALB 로 프록시해 same-origin 으로 API 에 닿고(ALPHA-615), 배포 계정 비밀번호는
Secrets Manager 시크릿으로 주입된다(ALPHA-618) — 로그인하면 실데이터가 뜬다.

## 라우트 / IA (디자인 v0.1)

`AdminLayout`(다크 사이드바 + 헤더) 하위 단일 레이아웃:

| 경로 | 화면 |
|---|---|
| `/login` | 운영자 로그인 (레이아웃 밖 공개 라우트 — 세션 만료·서버 오류·차단 배너 포함) |
| `/` | `/ops/incidents`로 이동 — 규칙 엔진 사건 목록이 첫 화면 |
| `/ops/incidents` | 파이프라인 문제·사건 목록 |
| `/ops/incidents/detail?vid=` | 사건 상세 (점 든 대상도 안전하도록 식별자는 쿼리) |
| `/ops/runs` · `/ops/runs/:runId` | 런·작업 귀결 목록 · 실행 상세 |
| `/ops/datasets` · `/ops/trend` | 데이터셋 신선도 · 산출/품질 추이 |
| `/ops/chain` · `/ops/delivery` | 설명 생성 흐름 · Cloud 게시/발번 경계 |
| `/ops/summary` | 파이프라인 개요 — **은퇴**(라우트·컴포넌트만 존치). 사이드바·인바운드 링크 없음이 의도다 |
| `/overview` | 레인 원장 요약 (구 첫 화면) — **은퇴**(라우트·컴포넌트만 존치). 개요는 자기 질문("지금 정상인가")에 답할 수 없어 메뉴에서 뺐다 |
| `/tenants` | 테넌트 목록 (검색·상태 필터 + 테넌트 생성 모달) |
| `/tenants/:id` | 테넌트 상세 (기본 정보 · 연결 상태 · 24H 호출 바 차트) |
| `/sources` | 데이터 소스 수집 상태 |
| `/grid` | 파이프라인 실행 이력 (슬롯×작업 30일 격자, 레인 필터 전체/시장/뉴스 — 셀 클릭 시 `/sources?runKey=` 드릴다운) |
| `/minute` | 장중 1분 수집 (세션 생존·창 집계·결손 창 목록 — 무증거 vs 빈 데이터 구분, ALPHA-651) |
| `/lineage/news` | 뉴스 계보 (funnel 타일 N/M%+(i) 산출 정의·단계 필터·언론사·원문 링크·1분 추출 요약 — ALPHA-685·697) |
| `/impact/holdings` | 구성종목 결손 영향 (누락 ETF → 기준일 분석 지목 + 권장 조치 — ALPHA-686) |
| `/analyses` | 가격 변동 분석 목록 (종목별 최신 / 분석 이력 두 보기 · 검색·상태·시장 필터) |
| `/analyses/symbol?market=&code=` | 종목 분석 이력 (최신 유효 설명 + 그 종목의 시도 전량 — ALPHA-738) |
| `/analyses/:id` | 가격 변동 분석 상세 (근거 · 영향도 · 무효화. 구 정정/제외/복원은 ALPHA-737 로 은퇴) |

⚠️ **종목 이력은 시장·코드를 경로가 아니라 쿼리로 받는다.** CloudFront 의 SPA fallback
([`spa-rewrite.js`](../../../../infra/terraform/modules/static-site/spa-rewrite.js))이 "마지막 경로
조각에 점(`.`)이 있으면 정적 파일"로 가르기 때문에, 점 든 티커(`BRK.B` 류)를 경로에 두면
**그 종목의 공유 링크·새로고침만** index.html 을 못 받는다. 주소 조립은
`domains/analyses/symbols.symbolHref` 한 곳이다.

미매칭(`*`)은 `/`를 거쳐 `/ops/incidents`로 리다이렉트(미인증이면 가드가 `/login` 으로).
2단계 인증(OTP) 뷰는 시안에 있으나 서버 2FA 미지원이라 범위 밖(ALPHA-474 계열 후속).
**신규 IA 금지 항목 준수**: API Key 관리 메뉴 없음 · 테넌트 사용 중지/재개 버튼 없음 (epic ALPHA-424).

## 규칙 엔진 (`src/rules/`)

콘솔 홈을 "규칙이 사실 위에서 돌아 나온 결과의 렌더링"으로 바꾸기 위한 **판정 층**이다
(ALPHA-738 · [ADR-0050](../../../../docs/adr/0050-console-facts-endpoint.md)). 규칙 R01~R19 +
인과 간선 + `evaluate()` 가 여기 살고 UI 를 모른다.

`pages/ops/*` 화면과 `consoleFacts` 어댑터가 이 결과를 소비한다. 와이어 DTO와 엔진 `Facts`의
형상 차이(camelCase vs snake_case), 응답 값 검증, 배치 사실과 `/sources/minute` 실시간 축의
결합은 그 어댑터·공통 훅에서 한 번만 처리한다.

알려진 결함·설계 노트·계측 부채는 [`src/rules/README.md`](src/rules/README.md)가 정본이다.

## 화면 기반 조각 (`src/styles/` · `src/pages/_shared/` · `src/mock/` · `domains` 파생 모듈)

화면이 올라설 토대다(ALPHA-738 조각 2). Overview·뉴스 계보·분석 목록·종목 이력과
`pages/ops/*` 규칙 엔진 화면이 이 조각들을 import 하므로 번들에 들어간다.

| 자리 | 무엇 |
|---|---|
| `styles/` 5 | ops·grid·minute·info-popover·mock-preview |
| `pages/_shared/` | `InfoPopover`(portal 팝오버 — 포커스·Escape 처리) · `MockPreview` |
| `mock/preview.ts` | 실 데이터 0건일 때 화면을 검수하는 미리보기 픽스처. `mock/preview.test.ts` 가 **서버가 낼 수 있는 응답인가**를 고정한다 |
| `domains/sources/` 파생 6 | `dailyRollup`(데이터셋×날짜 롤업 + 세션별 실행체 상태) · `datasetCatalog`(행 축) · `holdingsFlow`(구성종목 최종 완전성) · `minuteView`(1분 세션 표현 + `hasNoSignal`·`healthyClaimed`) · `lanes`(레인 코드→표시 이름) · `taskView`(작업 outcome×시도 → 라벨·톤) |
| `domains/analyses/symbols` | 분석 이력을 종목당 한 행으로 접는다 + 종목 상세 주소(`symbolHref`) |
| `layouts/headerRoute` | 경로 → 헤더 화면명·뒤로가기 목적지 |
| `pages/ops/` 판정 10 | 운영 조사 화면의 판정을 JSX 밖에 둔 것(ALPHA-738 조각 4). `consoleFacts`(와이어 DTO 검증 경계 + `Facts` 어댑터) · `investigation`(사건→조사 경로·딥링크 주소) · `notRun`(못 돎·조회 상태 어휘) · `runObservation`(원장↔AWS 두 관측 대조) · `datasetFreshness`(신선도 + 롤업 배지) · `trendCatalog`·`trendMetrics`·`trendSeries`·`trendAsOf`(추이 지표·계열·as-of 표기) · `newsFunnelSnapshot`(응답 밖 퍼널 스냅샷) |

⚠️ **판정을 `.tsx` 에 두지 않는다.** `pnpm --filter super-admin-ui test` 는 `node --test
'src/**/*.test.ts'` 라 **`.tsx` 를 아예 안 집는다** — 화면 파일 안의 분기는 변이를 걸어도 하나도
안 잡힌다. 그래서 위 표의 순수 모듈들이 존재한다(이 트랙에서 같은 이유로 여러 번 내렸다).

⚠️ **`datasetCatalog` 의 정본은 파이프라인 소스다** — `data_pipeline/ops/catalog.py`(작업 어휘)와
`data_pipeline/minute/states.py`(1분 dataset 어휘). 두 언어를 잇는 자동 가드가 없어
`datasetCatalog.test.ts` 가 그 다리다(유령·누락·중복을 양방향으로 잡는다). facts-snapshot 을
정본으로 쓰면 안 된다 — 날짜 고정 픽스처라 이후의 레인 이동·신설을 모른다.

## 데이터 레이어

화면 데이터는 전 도메인이 **super-admin-api 호출**이다(ALPHA-515). mock 데이터는
UI 가 아니라 API 쪽 `mock` 패키지가 반환하며, mock→DB 전환도 API 쪽에서 도메인
단위로 진행된다 — UI 는 그 전환을 알지 못한다(계약 불변).

tenant-console-ui 와 거의 동일 규약 — 공통 fetch 래퍼 [`src/api/client.ts`](src/api/client.ts)
(baseURL `/api/v1` · 에러 정규화 · 세션 쿠키 인증), TanStack Query hook, 페이지는 도메인
hook 만 의존. 도메인: `tenants` · `sources` · `analyses` · `session` · `console`.
super-admin-api 성공 응답도 공통 봉투(`ApiResponse`)라 client.ts 가 `.result` 를
중앙에서 벗겨 반환한다 — tenant-console-ui 도 동일하다(ALPHA-521·522, 도메인별
repository 는 무변경). 그 밖 상세 규약은
[tenant-console-ui README](../../onprem/tenant-console-ui/README.md#데이터-레이어-핵심-규약) 참조 (중복 서술하지 않는다).
