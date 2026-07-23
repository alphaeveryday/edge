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
되어 세션 쿠키(SameSite=Strict)가 실린다. 로그인 화면이 아직 없어(시안 미수령) 앱
진입 시 [`src/api/devSession.ts`](src/api/devSession.ts)가 데모 부트스트랩 계정
(`VITE_DEV_LOGIN_EMAIL`/`VITE_DEV_LOGIN_PASSWORD`, 기본 `operator@edge.local`)으로
자동 로그인해 세션을 확보한다 — **vite dev 전용**(prod 번들에서는 정적으로 제거돼
자격증명이 실리지 않는다), 로그인 화면 도입 시 제거한다. 정적 배포본(S3/CloudFront,
`admin-dev.edgesignal.dev`)은 `/api` 오리진 연결·로그인 화면이 아직 없어 화면
데이터가 비어 있다 — tenant-console-ui 와 같은 한계로, 배포 오리진 연결과 로그인
화면(시안 수령 후)이 후속이다.

## 라우트 / IA (디자인 v0.1)

`AdminLayout`(다크 사이드바 + 헤더) 하위 단일 레이아웃:

| 경로 | 화면 |
|---|---|
| `/tenants` | 테넌트 목록 (검색·상태 필터 + 테넌트 생성 모달) |
| `/tenants/:id` | 테넌트 상세 (기본 정보 · 연결 상태 · 24H 호출 바 차트) |
| `/sources` | 데이터 소스 수집 상태 |
| `/analyses` | 가격 변동 분석 목록 (검색·상태·시장 필터) |
| `/analyses/:id` | 가격 변동 분석 상세 (근거 · 영향도 · 정정 · 제외/복원) |

진입(`/`)·미매칭(`*`)은 `/tenants` 로 리다이렉트. 운영자 인증 화면은 시안 미수령으로 없다 (후속).
**신규 IA 금지 항목 준수**: API Key 관리 메뉴 없음 · 테넌트 사용 중지/재개 버튼 없음 (epic ALPHA-424).

## 데이터 레이어

화면 데이터는 전 도메인이 **super-admin-api 호출**이다(ALPHA-515). mock 데이터는
UI 가 아니라 API 쪽 `mock` 패키지가 반환하며, mock→DB 전환도 API 쪽에서 도메인
단위로 진행된다 — UI 는 그 전환을 알지 못한다(계약 불변).

tenant-console-ui 와 동일 규약 — 공통 fetch 래퍼 [`src/api/client.ts`](src/api/client.ts)
(baseURL `/api/v1` · 에러 정규화 · 세션 쿠키 인증), TanStack Query hook, 페이지는 도메인
hook 만 의존. 도메인: `tenants` · `sources` · `analyses` · `session`.
상세 규약은 [tenant-console-ui README](../../onprem/tenant-console-ui/README.md#데이터-레이어-핵심-규약) 참조 (중복 서술하지 않는다).
