# ADR-0008: super-admin 콘솔 — cross-tenant 운영자 표면

- 상태: 승인됨
- 날짜: 2026-06-27

## 맥락
`tenant-console`(ui/api)는 **한 테넌트 내부 범위**다 — 테넌트 직원이 로그인해 자기 테넌트의 설정·데이터만 다룬다(현행 구조는 [context.md](../context.md), 결정 당시 서술은 구 architecture.md §1·§4 — 삭제됨).
그런데 플랫폼 운영자(우리)가 **모든 테넌트를 가로질러** 해야 하는 일 — 테넌트 프로비저닝/정지, 전역 설정, 장애 대응, 감사·점검 — 을 담을 표면이 없다.

이 cross-tenant 권한을 `tenant-console-api`에 얹으면 안 된다. 테넌트 직원 세션이 닿는 서비스에 타 테넌트 데이터를 만지는 코드 경로가 생기면 **테넌트 격리가 무너진다**. 권한 범위가 다른 액터는 표면도 분리해야 한다 — [[0006-gateway-single-edge]]가 widget/console을 서비스 레벨에서 나눈 것과 같은 논리.

## 결정
**super-admin을 별도 컴포넌트로 둔다.** cross-tenant 운영자 전용 표면이다.

- **앱 분리** — `super-admin-ui`(Node) + `super-admin-api`(JVM)를 신설한다. `tenant-console-*`에 합치지 않는다. `super-admin-api`는 **cross-tenant 읽기/쓰기 = 시스템 최고 권한 표면**이다.
- **gateway 세 번째 라우트 체인(admin)** — gateway는 단일 엣지를 유지하되([[0006-gateway-single-edge]]), widget·console에 더해 **admin 체인**을 추가한다. 호스트/경로로 분리(예: `admin.*` 또는 `/admin/*`), 기본 **fail-closed**, 운영자 세션 인증.
- **네트워크 수준 제한** — 운영자는 **소수·알려진 집합**이라 테넌트 직원과 달리 공개 인터넷 노출이 필요 없다. admin 라우트는 **VPN/IP allowlist**로 추가 제한한다. 즉 admin 체인은 엣지 정책 + 망 제한의 이중 게이트다.
- **엣지에만 의존하지 않는다** — `super-admin-api`도 서비스 레벨에서 운영자 인증·인가를 요구한다(엣지/망 통과만으로 접근 불가).

## 대안
- **`tenant-console-api`에 운영자 역할만 추가** — 새 앱 없이 끝나지만, 테넌트 직원이 닿는 서비스에 cross-tenant 권한이 섞여 **테넌트 격리·표면 경계가 붕괴**한다. 폐기.
- **완전 별도 엣지(독립 gateway)** — 물리적 분리는 더 세지만, TLS·레이트리밋·WAF·관측 같은 엣지 정책을 **중복 구성**해야 하고 [[0006-gateway-single-edge]]의 단일 엣지 일관성이 깨진다. 단일 엣지 + 전용 fail-closed 체인 + 망 제한으로 충분하다.

## 결과
- 최고 권한(cross-tenant) 표면이 분리되어 **테넌트 격리가 보존**된다.
- gateway는 이제 widget·console·admin **3개 라우트 체인**을 가진다. admin 체인의 fail-closed·망 제한은 반드시 지켜야 할 의무다 — gateway 설정 변경은 더 신중히 리뷰한다.
- `README.md`에서 `tenant-console-ui`를 가리키던 "내부 관리자 콘솔" 표현은 **super-admin이 진짜 운영자 콘솔**이 되면서 혼동을 일으키므로, tenant-console는 "**테넌트 직원 콘솔**"로 정정한다.
- 스캐폴드는 [[0001-monorepo-structure]] 컨벤션대로 빈 폴더를 `.gitkeep`으로 추적하고, 구현 전까지 미리 채우지 않는다.
