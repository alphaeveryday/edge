# ADR-0006: gateway 단일 엣지 · 라우트별 신뢰 필터

- 상태: 대체됨(→ [ADR-0010](0010-hybrid-onprem-pivot.md))
- 날짜: 2026-06-21

## 맥락
widget(공개·읽기전용·좁은 표면)과 console(인증·읽기쓰기·넓은 표면)은 신뢰 수준이 다르지만,
**둘 다 인터넷을 통해 접근**된다(콘솔도 테넌트 직원이 브라우저로 접속). 따라서 TLS 종료·레이트리밋·
WAF·관측·인증 검증 같은 엣지 정책을 양쪽에 적용해야 한다. 초기 구상은 gateway가 widget만 앞단에 두고
console은 `tenant-console-ui → tenant-console-api`로 직접 가는 형태였다.

여기서 핵심 우려는, 넓은 read/write 표면인 console을 공개 표면과 한 컴포넌트가 공유할 때
**필터 오설정 하나가 console을 공개로 노출**할 수 있다는 점이다.

## 결정
**gateway를 단일 엣지**로 두고 widget·console 트래픽을 모두 받는다. 단, 경계를 다음으로 보존한다:
- **호스트/경로로 분리**한다(예: `widget.*` vs `app.*`, 또는 `/widget/*` vs `/console/*`).
- **라우트별 독립 필터 체인**을 두고 기본값은 **fail-closed**로 한다.
  - widget 체인: 익명/위젯 토큰, 읽기(GET) 위주, 공격적 레이트리밋, 임베드용 CORS.
  - console 체인: 테넌트 사용자 세션 인증, 전체 메서드, 사용자/테넌트 단위 제한, CSRF 등.
- **백엔드 두 서비스는 분리를 유지**한다 — gateway가 앞단을 공유한다고 해서 `widget-api`와
  `tenant-console-api`를 합치지 않는다. 좁음/넓음·읽기전용/읽기쓰기 경계는 서비스 레벨에 남는다.
- **엣지에만 의존하지 않는다** — `widget-api`는 읽기 전용이라 오라우팅돼도 변경 불가,
  `tenant-console-api`는 서비스 레벨에서도 인증을 요구한다(엣지 통과만으로 접근 불가).

상세는 구 architecture.md §4 신뢰 경계 (하이브리드 피벗으로 삭제됨 — `git log --follow`로 열람 가능).

## 대안
- **gateway는 widget만, console은 직접** — 경계가 물리적으로 더 분리되지만, 엣지 정책(레이트리밋·WAF·
  관측)을 console 쪽에 **따로 중복 구성**해야 한다. 운영 일관성이 떨어진다.
- **widget-api와 tenant-console-api를 한 서비스로 병합** — 좁음/넓음 표면 경계가 무너진다. 폐기.

## 결과
- 엣지 정책을 **한 곳에서 일관**되게 운영한다.
- "단일 컴포넌트 공유 → 단일 실패점" 우려는 **서비스 레벨 방어**(읽기전용 widget-api, 인증 요구 console-api)로 완화된다. 즉 다층 방어.
- 대신 라우트별 격리·fail-closed·호스트 분리를 **반드시 지켜야 할 의무**가 생긴다. gateway가 더 중요한 컴포넌트가 되므로 설정 변경은 신중히 리뷰한다.
- 서비스 분리·DB 통합 전제는 [[0001-monorepo-structure]]·[[0005-db-as-contract]]와 일관된다.
