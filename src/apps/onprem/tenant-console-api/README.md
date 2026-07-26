# tenant-console-api

증권사 On-Premise 콘솔의 백엔드 — 검수 표면(Review Queue)·인증 + 콘솔 화면
표면(현재 mock). 계약·권한의 SSOT 는 [docs/console-ia/](../../../../docs/console-ia/)이고,
이 README 는 이 모듈만의 비자명한 규율만 적는다. 실 DB 접근은 **JPA**(entity·좁은
Spring Data repository·도메인 model 매핑)다 — DDL 은 Flyway(libs/schema/migrations-onprem)가
SSOT 이므로 Hibernate 는 스키마를 만들지 않고 검증만 한다(`ddl-auto=validate`·`flyway.enabled=false`).
소유 전이 쓰기(검수 결정 status 전이·publication 재발행)는 원자성 가드가 붙은 native
`@Modifying` 쿼리다. ALPHA-525·526 과 함께 진행한 JdbcTemplate→JPA 전환의 일부다(ADR-0038).

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
  인가는 세션 캐시가 아니라 **매 요청 원장(member) 재검증**이다(ALPHA-119) —
  비활성화는 세션 무효화 후 401, 역할 변경(ALPHA-499)은 다음 요청부터 즉시 반영.
  CSRF 는 세션 쿠키 SameSite=Strict·HttpOnly 로 경량 방어(운영은 표준 토큰 추가).

## 콘솔 mock 표면 (ALPHA-513)

tenant-console-ui 도메인 계약(repository.real.ts)과 1:1 인 화면 표면 중 mock 잔여
3종 — explanations(가격 변동 설명·반입 상태) · screening(금칙어·기준·면책 문구) ·
scope(시장·종목 제공 범위). members(사용자 관리)는 ALPHA-119 로 member 원장
실데이터로 전환됐고(등록·목록·비활성화 + 역할 변경 ALPHA-499), session 은
ALPHA-500 으로 실전환됐다 — name 은 인증 주체(member 원장), 테넌트 컨텍스트는
배포 설정(`console.tenant.*`, 온프렘 박스=테넌트 1:1)이 소스다.

- **응답 원천은 `mock` 패키지** — 도메인별 in-memory 가변 스토어(`*MockStore`) 한
  파일이 UI 구 mock 데이터의 이식본이다. DB 연동은 도메인 단위로 service 의 스토어
  의존을 repository 로 교체하는 방식으로 진행한다(UI 는 계약 불변이라 무변경).
- **와이어 타입은 `dto` 패키지** — 요청·응답 계약은 `dto` 의 `XxxRequest`/
  `XxxResponse` record 이고, 컨트롤러가 `XxxResponse.from(스토어/도메인 record)` 로
  매핑해 반환한다(서비스는 여전히 mock/도메인 record 반환). mock record(스토어 형)와
  형식이 같아도 별도 타입이다 — DB 연동 시 `from()` 의 매핑원이 mock record 에서
  repository record 로 바뀐다. 네이밍(`Xxx{Request,Response}`, `Dto` 접미사 없음)은
  tenant-sync-api·publication-api·super-admin-api(ALPHA-523) dto 규약을 따른다.
  평면 패키지라 필드가 다른 두 반려 요청은 `ReviewRejectRequest`(reason)·
  `ExplanationRejectRequest`(note)로 도메인 접두어를 붙였다(ALPHA-524).
- **JSON 은 camelCase** — UI 타입이 계약의 SSOT 라 기존 검수 표면(snake_case)과
  다르다. `final` 은 Java 예약어라 컴포넌트명은 `finalText`, JSON 은 `@JsonProperty`.
- **성공·에러 모두 공통 응답 포맷(`ApiResponse`)** — 콘솔 전 표면이 jvm-common 봉투
  `{isSuccess,code,message,result}` 로 내려간다. 계약 DTO 는 `result` 안에 있고(검수·인증
  표면은 snake_case·ALPHA-513 표면은 camelCase — 네이밍은 위 규약대로), 뮤테이션도 200 +
  `result` 생략이다(204 는 쓰지 않는다). 성공까지 봉투로 감싸는 건 콘솔 계열 API 규약이다
  — tenant-console-api·super-admin-api 가 채택했다(ALPHA-521·522). 실계약 조회 표면
  (tenant-sync-api·publication-api)은 raw DTO 성공을 유지하는 의도적 분기다(AGENTS Rule 7·11).
- **인가는 인증만 강제(전 역할)** — 로그인 화면 없는 mock 단계의 한시 예외
  (permission-matrix.md "콘솔 mock 표면" 절). 도메인 DB 전환 시 역할을 좁힌다.

## 스텁 → 실구현 교체 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| `mock` 패키지 `*MockStore` 3종 | 도메인별 DB 연동 | service 의존을 repository 로 교체 + 필터 역할 세분화 |
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

테스트 91건 — 검수 계약(승인=전이+재발행, 반려 사유 필수, 409 수렴), 인증
계약(로그인 성공/실패 동일 코드·SSO 전용 거부, 필터 401/403·역할 강제·matrix
parameter 우회 차단·매핑 부재 fail-closed·세션 주체=원장 정체성 SSOT, 부트스트랩
멱등·해시 저장), 사용자 관리 계약(등록 검증·중복 409·마지막 관리자 409, 역할
변경의 자기변경 403·조건부 갱신 409·감사 기록), 세션 계약(주체 이름·설정 테넌트
컨텍스트·프로필 원장 기록·길이 상한 400), 콘솔 mock 표면의 UI 계약(camelCase·
`final` 필드·상태 전이·어휘 게이트·404)을 인코딩한다.
단위 테스트는 리포지토리(좁은 인터페이스)를 페이크로 스텁해 DB 없이 돈다. DB 계약은
Testcontainers Postgres + Flyway(migrations-onprem) 통합 테스트가 검증한다 —
`contextLoads` 가 `ddl-auto=validate` 로 엔티티↔실스키마 정합을, `ReviewMemberRepositoryIT`
가 decide 가드·publish ON CONFLICT·활성 조회·save 를 실 쿼리로 확인한다(Docker 없으면
JUnit `@EnabledIf` 로 disabled 로 보고 — 숨겨진 통과가 아니다; CI/Docker 에서 실행).
