# tenant-console-ui

내부 관리자 콘솔(EDGE Console). Vite + React 19 + TypeScript + Tailwind CSS v4 + TanStack Query,
디자인 시스템은 워크스페이스 패키지 [`ui-kit`](../../../libs/ui-kit)를 공유한다.
화면은 claude.ai/design 시안 **v0.2** (EDGE Wireframe Design System) 기준.

## 실행

```bash
# 백엔드 먼저 (레포 루트에서) — 콘솔 API 는 fail-closed 라 API 없이는 화면 데이터가 비어 있다
docker compose up --build tenant-console-api   # host 18081

pnpm --filter tenant-console-ui dev      # http://localhost:5174
pnpm --filter tenant-console-ui build
pnpm --filter tenant-console-ui typecheck
```

dev 서버는 `/api` 를 tenant-console-api(기본 `http://localhost:18081`, bootRun 직접
기동이면 `VITE_API_PROXY_TARGET=http://localhost:8080`)로 프록시한다 — same-origin 이
되어 세션 쿠키(SameSite=Strict)가 실린다. 로그인 화면이 아직 없어(ALPHA-486 범위 밖)
앱 진입 시 [`src/api/devSession.ts`](src/api/devSession.ts)가 데모 부트스트랩 계정
(`VITE_DEV_LOGIN_EMAIL`/`VITE_DEV_LOGIN_PASSWORD`, 기본 `admin@demo.edge.local`)으로
자동 로그인해 세션을 확보한다 — **vite dev 전용**(prod 번들에서는 정적으로 제거돼
자격증명이 실리지 않는다), 로그인 화면 도입 시 제거한다. 정적 배포본(S3/CloudFront)은
`/api` 오리진이 아직 없어 데이터가 비어 있다 — 데모 런타임 오리진 연결은 ALPHA-445 후속.

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

화면 데이터는 전 도메인이 **tenant-console-api 호출**이다(ALPHA-513). mock 데이터는
UI 가 아니라 API 쪽 `mock` 패키지가 반환하며, mock→DB 전환도 API 쪽에서 도메인
단위로 진행된다 — UI 는 그 전환을 알지 못한다(계약 불변).

- 페이지/컴포넌트는 repository 를 직접 import 하지 않는다. 도메인 hook(예: `useExplanations`)만 의존한다.
- 공통 fetch 래퍼는 [`src/api/client.ts`](src/api/client.ts) (baseURL `/api/v1` · 에러 정규화 · 세션 쿠키 인증).
  tenant-console-api 성공 응답은 공통 봉투(`ApiResponse` — `{isSuccess,code,message,result}`)라, 래퍼가
  `.result` 를 중앙에서 벗겨 반환한다 — 도메인별 repository·mock 은 봉투를 알지 못한다(계약 불변). ALPHA-522.
- hook 은 **TanStack Query** 기반 — mutation 성공 시 해당 도메인 쿼리를 invalidate 해 화면이 갱신된다.

### 도메인 구조 (`src/domains/<domain>/`)

```
types.ts             API 와 공유하는 계약 타입 (상태 코드는 state-machine 어휘: AUTO_PUBLISHED 등)
repository.ts        interface (계약)
repository.real.ts   api client 사용 (tenant-console-api 콘솔 표면과 1:1)
index.ts             real repository export
hooks.ts             페이지가 쓰는 hook (TanStack Query)
```

도메인: `explanations`(가격 변동 설명·반입 상태) · `screening`(점검 기준) · `scope`(제공 범위) ·
`users`(사용자·권한) · `session`(로그인 사용자·테넌트) · `dashboard`(제공 API 트래픽 KPI, ALPHA-128).
한글 라벨·배지 톤 매핑은 `explanations/labels.ts` (뷰 관심사 — 도메인 코드와 분리).

## 스타일 규약

- 토큰·컴포넌트 클래스(`ui-kit/styles.css`) + Tailwind 유틸리티(레이아웃)만 사용한다.
- 앱 자체 전역 CSS 는 [`src/styles/app.css`](src/styles/app.css) (tailwind 진입점) 하나뿐이다 — 구 `global.css` 체계는 폐기.
- import 순서는 tailwind(preflight) → ui-kit 고정 (`src/main.tsx`).

샘플 데이터는 가상 증권사 **KB증권** 기준 — 시안 목데이터는 tenant-console-api 의
`mock` 패키지로 이식됐다.
