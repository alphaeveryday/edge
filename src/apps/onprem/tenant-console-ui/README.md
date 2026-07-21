# tenant-console-ui

내부 관리자 콘솔(EDGE Console). Vite + React 19 + TypeScript + Tailwind CSS v4 + TanStack Query,
디자인 시스템은 워크스페이스 패키지 [`ui-kit`](../../../libs/ui-kit)를 공유한다.
화면은 claude.ai/design 시안 **v0.2** (EDGE Wireframe Design System) 기준.

## 실행

```bash
pnpm --filter tenant-console-ui dev      # http://localhost:5174
pnpm --filter tenant-console-ui build
pnpm --filter tenant-console-ui typecheck
```

## 라우트 / IA (디자인 v0.2)

`ConsoleLayout`(다크 사이드바 + 헤더) 하위 단일 레이아웃:

| 경로 | 화면 |
|---|---|
| `/dashboard` | 대시보드 (KPI 6종 + 최근 설명 요약) |
| `/explanations` · `/explanations/:id` | 가격 변동 설명 목록·상세 (최종 문구 수정 · 검수 이관 · 제공 중단) |
| `/review` · `/review/:id` | 검수 대기 목록·상세 (임시 저장 · 반려 · 승인 후 제공) |
| `/screening` | 점검 기준 관리 — 금칙어 · 점검 처리 기준 · 면책 문구 3탭 |
| `/scope` | 제공 범위 설정 (시장·종목 토글) |
| `/users` | 사용자 및 권한 (+ 초대 모달) |

진입(`/`)·미매칭(`*`)은 `/dashboard` 로 리다이렉트.
인증·온보딩 화면은 시안 미수령으로 현재 IA에 없다 (후속 — ALPHA-486 범위 밖).

## 데이터 레이어 (핵심 규약)

화면 데이터는 mock 으로 시작하되 **도메인 단위로** 실연동 교체가 가능하다.

- 페이지/컴포넌트는 **mock 을 직접 import 하지 않는다.** 도메인 hook(예: `useExplanations`)만 의존한다.
- 도메인 스위치는 [`src/config/dataSources.ts`](src/config/dataSources.ts) 한 곳. 해당 도메인 한 줄을 `'mock' → 'real'` 로 바꾸면 교체된다.
- 공통 fetch 래퍼는 [`src/api/client.ts`](src/api/client.ts) (baseURL · 인증 헤더 · 에러 정규화).
- hook 은 **TanStack Query** 기반 — mutation 성공 시 해당 도메인 쿼리를 invalidate 해 화면이 갱신된다.
  mock 도 반드시 `Promise` 를 반환한다(async) — mock·real 의 로딩/에러 처리 모양을 동일하게 유지하기 위함.

### 도메인 구조 (`src/domains/<domain>/`)

```
types.ts             mock·real 공유 타입 (상태 코드는 state-machine 어휘: AUTO_PUBLISHED 등)
repository.ts        interface (계약)
repository.mock.ts   mock 구현 (Promise 반환, 모듈 레벨 가변 스토어)
repository.real.ts   api client 사용 (현재 stub)
index.ts             config 보고 mock|real 중 하나 export
hooks.ts             페이지가 쓰는 hook (TanStack Query)
```

도메인: `explanations`(가격 변동 설명·반입 상태) · `screening`(점검 기준) · `scope`(제공 범위) ·
`users`(사용자·권한) · `session`(로그인 사용자·테넌트).
한글 라벨·배지 톤 매핑은 `explanations/labels.ts` (뷰 관심사 — 도메인 코드와 분리).

## 스타일 규약

- 토큰·컴포넌트 클래스(`ui-kit/styles.css`) + Tailwind 유틸리티(레이아웃)만 사용한다.
- 앱 자체 전역 CSS 는 [`src/styles/app.css`](src/styles/app.css) (tailwind 진입점) 하나뿐이다 — 구 `global.css` 체계는 폐기.
- import 순서는 tailwind(preflight) → ui-kit 고정 (`src/main.tsx`).

샘플 데이터는 가상 증권사 **KB증권** 기준 (시안 목데이터 이식).
