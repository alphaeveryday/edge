# tenant-console-ui

내부 관리자 콘솔(EDGE Console). Vite + React 19 + TypeScript, 순수 CSS, `react-router-dom` 레이아웃 라우트.

## 실행

```bash
pnpm --filter tenant-console-ui dev      # http://localhost:5174
pnpm --filter tenant-console-ui build
pnpm --filter tenant-console-ui typecheck
```

## 라우트 / 레이아웃 (IA)

| 레이아웃 | 경로 |
|---|---|
| `AuthLayout` | `/login` · `/invite/:token` · `/forgot-password` |
| `OnboardingLayout` | `/onboarding/*` |
| `ConsoleLayout` (Sidebar + Topbar) | `/dashboard` · `/applications` · `/usage` · `/compliance` · `/members` · `/settings` |
| `ApplicationLayout` (탭) | `/applications/:appId/{overview\|analysis\|widget\|webhooks}` |

진입(`/`)·미매칭(`*`)은 `/login` 으로 리다이렉트.

## 데이터 레이어 (핵심 규약)

화면 데이터는 mock 으로 시작하되 **도메인 단위로** 실연동 교체가 가능하다.

- 페이지/컴포넌트는 **mock 을 직접 import 하지 않는다.** 도메인 hook(예: `useMembers`)만 의존한다.
- 도메인 스위치는 [`src/config/dataSources.ts`](src/config/dataSources.ts) 한 곳. 해당 도메인 한 줄을 `'mock' → 'real'` 로 바꾸면 교체된다.
- 공통 fetch 래퍼는 [`src/api/client.ts`](src/api/client.ts) (baseURL · 인증 헤더 · 에러 정규화).
- mock 도 반드시 `Promise` 를 반환한다(async) — mock·real 의 로딩/에러 처리 모양을 동일하게 유지하기 위함.

### 도메인 구조 (`src/domains/<domain>/`)

```
types.ts            mock·real 공유 타입
repository.ts        interface (계약)
repository.mock.ts   mock 구현 (Promise 반환)
repository.real.ts   api client 사용 (현재 stub)
index.ts             config 보고 mock|real 중 하나 export
hooks.ts             페이지가 쓰는 hook
```

레퍼런스 구현: [`src/domains/members`](src/domains/members) → [`src/pages/MembersPage.tsx`](src/pages/MembersPage.tsx).
나머지 도메인(dashboard·applications·usage·compliance·settings)은 같은 패턴으로 확장 예정이며, 현재는 플레이스홀더 화면이다.

샘플 데이터는 가상 증권사 **한빛투자증권** 기준.
