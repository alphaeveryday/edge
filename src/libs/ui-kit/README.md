# ui-kit

콘솔 UI(tenant-console-ui · super-admin-ui) 공유 디자인 시스템.
claude.ai/design "EDGE Wireframe Design System"(프로젝트 f62e29bd)의 이식본 — 토큰·컴포넌트 값 변경은 디자인 시스템과 함께 간다.

## 구성

```
src/styles/tokens.css       디자인 토큰 (그레이스케일·액센트·방향 시맨틱·타입·간격) — colors_and_type.css 이식
src/styles/components.css   컴포넌트 클래스 (.btn·.card·.table·.nav-item·.switch 등) — edge-components.css 이식
src/styles/index.css        스타일 진입점 (tokens → components)
src/*.tsx                   React 프리미티브: StatusBadge · Toggle · Modal · Toaster/toast · Icon · Delta
```

## 소비 방법 (소스 패키지)

빌드 산출물이 없는 **소스 export 패키지**다 — 앱의 Vite 가 TSX 를 직접 컴파일한다.

```jsonc
// 앱 package.json
"dependencies": { "ui-kit": "workspace:*" }
```

```ts
import 'ui-kit/styles.css';            // tailwind(preflight) 뒤에 import — preflight가 토큰을 덮지 않게
import { StatusBadge, toast } from 'ui-kit';
```

- 단순 위젯(버튼·입력·카드·테이블)은 React 래퍼 없이 **클래스 직접 사용**이 기본이다 (`<button className="btn btn-primary">`). 상태·로직이 있는 것만 컴포넌트로 제공한다.
- 아이콘은 Lucide 서브셋 인라인 — CDN 미사용. 새 아이콘은 `Icon.tsx`의 PATHS에 추가한다.
