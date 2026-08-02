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
```

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
| `/` | 오늘 운영 현황 — Run Overview (레인별 최신 런 요약, 첫 화면 — ALPHA-683) |
| `/tenants` | 테넌트 목록 (검색·상태 필터 + 테넌트 생성 모달) |
| `/tenants/:id` | 테넌트 상세 (기본 정보 · 연결 상태 · 24H 호출 바 차트) |
| `/sources` | 데이터 소스 수집 상태 |
| `/grid` | 파이프라인 실행 이력 (슬롯×작업 30일 격자 — 셀 클릭 시 `/sources?runKey=` 드릴다운) |
| `/lineage/news` | 뉴스 계보 (수집→증거→분석 사용 집계 + 근거 문서 표본, KST 수집일 필터 — ALPHA-685) |
| `/analyses` | 가격 변동 분석 목록 (검색·상태·시장 필터) |
| `/analyses/:id` | 가격 변동 분석 상세 (근거 · 영향도 · 정정 · 제외/복원) |

미매칭(`*`)은 `/`(Run Overview)로 리다이렉트(미인증이면 가드가 `/login` 으로).
2단계 인증(OTP) 뷰는 시안에 있으나 서버 2FA 미지원이라 범위 밖(ALPHA-474 계열 후속).
**신규 IA 금지 항목 준수**: API Key 관리 메뉴 없음 · 테넌트 사용 중지/재개 버튼 없음 (epic ALPHA-424).

## 데이터 레이어

화면 데이터는 전 도메인이 **super-admin-api 호출**이다(ALPHA-515). mock 데이터는
UI 가 아니라 API 쪽 `mock` 패키지가 반환하며, mock→DB 전환도 API 쪽에서 도메인
단위로 진행된다 — UI 는 그 전환을 알지 못한다(계약 불변).

tenant-console-ui 와 거의 동일 규약 — 공통 fetch 래퍼 [`src/api/client.ts`](src/api/client.ts)
(baseURL `/api/v1` · 에러 정규화 · 세션 쿠키 인증), TanStack Query hook, 페이지는 도메인
hook 만 의존. 도메인: `tenants` · `sources` · `analyses` · `session`.
super-admin-api 성공 응답도 공통 봉투(`ApiResponse`)라 client.ts 가 `.result` 를
중앙에서 벗겨 반환한다 — tenant-console-ui 도 동일하다(ALPHA-521·522, 도메인별
repository 는 무변경). 그 밖 상세 규약은
[tenant-console-ui README](../../onprem/tenant-console-ui/README.md#데이터-레이어-핵심-규약) 참조 (중복 서술하지 않는다).
