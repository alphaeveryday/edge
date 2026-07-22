# tenant-console-api

증권사 On-Premise 콘솔의 백엔드 — 검수 표면(Review Queue)과 인증. 계약·권한의
SSOT 는 [docs/console-ia/](../../../../docs/console-ia/)이고, 이 README 는 이
모듈만의 비자명한 규율만 적는다. 다른 온프렘 모듈과 동일하게 JPA 없이
JdbcTemplate(thin layered).

## 지켜야 할 로컬 불변식

- **인가 게이트는 `ConsoleAuthFilter` 하나** — 미인증은 전 `/api/**` 표면 401
  (fail-closed). 라우트×역할 정책은 [permission-matrix.md](../../../../docs/console-ia/permission-matrix.md)
  "API 매핑" 표와 **1:1**이다. 엔드포인트를 추가하면 표와 필터 `RULES` 에 함께
  행을 더한다 — 표에 없는 표면은 인증돼도 403 거부가 기본이라, 매핑을 빼먹으면
  그 API 는 배포돼도 닫혀 있다(우회가 아니라 누락이 안전한 쪽으로 실패).
- **경로 매칭은 정규화 후** — 필터는 context-path 를 제거하고 세그먼트별 matrix
  parameter(`;k=v`)를 벗긴 경로로 판정한다. MVC 의 PathPattern 매핑과 같은 기준을
  써야 `/api;x=y/...` 같은 우회로 필터가 통째로 건너뛰어지지 않는다.
- **검수 전이 writer 분담** — 이 모듈은 `analysis_item` 의 검수 결정 전이
  (REVIEW_REQUIRED→APPROVED|REJECTED)만 쓴다(수신·자동 분기는 screening-worker).
  스키마 COMMENT 가 SSOT. `member` 는 이 모듈이 유일 writer.
- **승인 = 전이+재발행 단일 트랜잭션** — 승인됐는데 게시가 안 되는 어중간한
  상태를 남기지 않는다. grain 선점·ticker 결측은 409/전이 롤백으로 수렴.

## 인증 (ADR-0025 하이브리드)

- **데모 경로(구현됨)**: 자체 계정 로그인 — 이메일+비밀번호(BCrypt), 관리자 직접
  등록(셀프 가입·초대 없음). 로그인 성공 = 역할 실린 세션(`SessionMember`), 세션
  ID 재발급으로 고정(fixation) 차단. 실패 사유는 구분 없는 401 + 미존재 계정에도
  더미 BCrypt 비교(타이밍 오라클 차단).
- **부트스트랩**: `member` 0건일 때 `console.auth.bootstrap-accounts` 를 1회 시드
  (전 계정 한 트랜잭션 — 부분 시드 방지). 설정 결함(중복 이메일·잘못된 role)은
  기동을 실패시키고(fail-loud), DB 일시 미가용은 로그 후 첫 로그인 때 재시도한다.
- **운영 경로(설계)**: SSO/AD — 같은 `SessionMember` 로 수렴하는 별도 진입점,
  구현은 실계약 시점. `password_hash` NULL = SSO 전용 계정(로컬 로그인 거부).
- **의도적 생략(데모 범위)**: 로그인 레이트리밋·계정 잠금 없음(내부망 전제).
  세션 role 은 발급 시점 스냅샷이라 로그인 이후의 비활성화·역할 회수는 세션
  만료 전까지 반영되지 않는다 — 매 요청 재검증은 사용자 관리(ALPHA-119)와 함께.
  CSRF 는 세션 쿠키 SameSite=Strict·HttpOnly 로 경량 방어(운영은 표준 토큰 추가).

## 스텁 → 실구현 교체 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| 부트스트랩 시드(데모 자체 계정) | ALPHA-119 | 콘솔 Users & Roles 등록·역할 부여 화면·API |
| 데모 로그인 | 실증권사 계약 | SSO/AD(SAML/OIDC) 진입점 — 같은 세션 추상화로 수렴 |

## 실행·확인

```bash
# 루트에서 (온프렘 PG + 스키마 포함)
docker compose up --build tenant-console-api   # host 18081
curl localhost:18081/actuator/health
curl -i -X POST localhost:18081/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"reviewer@demo.edge.local","password":"demo-reviewer-1"}'   # 200 + 세션 쿠키
# bootRun 은 postgres-onprem(:55433) 이 떠 있어야 한다 (src/ 에서 :apps:onprem:tenant-console-api:bootRun)
```

테스트 21건 — 검수 계약(승인=전이+재발행, 반려 사유 필수, 409 수렴)과 인증
계약(로그인 성공/실패 동일 코드·SSO 전용 거부, 필터 401/403·역할 강제·matrix
parameter 우회 차단·매핑 부재 fail-closed, 부트스트랩 멱등·해시 저장)을 인코딩한다.
`contextLoads` 는 실 DB 를 요구하는 통합 테스트라 로컬 DB 없이는 실패한다(compose E2E 경로).
