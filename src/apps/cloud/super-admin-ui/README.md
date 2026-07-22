# super-admin-ui

> 역할/아키텍처는 루트 [README](../../../../README.md)·[docs/context.md](../../../../docs/context.md)가 SSOT.
> 이 문서는 로컬 실행·범위 경계만 둔다.

운영자 콘솔(EDGE Admin). tenant-console-ui 와 동일 스택 — Vite + React 19 + TypeScript +
Tailwind CSS v4 + TanStack Query, 디자인 시스템은 [`ui-kit`](../../../libs/ui-kit) 공유.
화면은 claude.ai/design 시안 **v0.1** (EDGE Wireframe Design System) 기준.

## 실행

Node 패키지 매니저는 **pnpm**이다(ADR-0001). Node 워크스페이스 루트는 `src/pnpm-workspace.yaml`.

```bash
pnpm --filter super-admin-ui dev        # http://localhost:5175
pnpm --filter super-admin-ui build      # tsc --noEmit && vite build → dist/
pnpm --filter super-admin-ui typecheck
```

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

tenant-console-ui 와 동일 규약 — 도메인 단위 mock↔real 스위치([`src/config/dataSources.ts`](src/config/dataSources.ts)),
TanStack Query hook, 페이지는 도메인 hook 만 의존. 도메인: `tenants` · `sources` · `analyses` · `session`.
상세 규약은 [tenant-console-ui README](../../onprem/tenant-console-ui/README.md#데이터-레이어-핵심-규약) 참조 (중복 서술하지 않는다).
