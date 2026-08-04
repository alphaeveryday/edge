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
  (REVIEW_REQUIRED→APPROVED|REJECTED|BLOCKED, ALPHA-437)만 쓴다(수신·자동 분기는
  screening-worker — BLOCKED 도 전이 원점이 다르다: worker=자동 분기, 콘솔=검수
  차단). 스키마 COMMENT 가 SSOT. `member` 는 이 모듈이 유일 writer 이고,
  `review_task`(생성·결정)·`analysis_item_status_history`(MEMBER 전이)도 분담
  범위만 쓴다(CANCELLED·SYSTEM 은 screening-worker).
- **검수 결정 = 전이+재발행+기록+감사 단일 트랜잭션** — 승인됐는데 게시가 안
  되거나, 결정이 기록(review_task·status_history·console_action_log) 없이 남는
  어중간한 상태를 만들지 않는다. ticker 결측·같은 스냅샷 이중 게시(grain=ticker,trade_date,as_of — ALPHA-743 공존 모델)는 409/전이 롤백으로 수렴.
  수정 승인(approve-edited)의 편집 문구는 `publication.published_summary` 스냅샷
  으로 게시된다(원문은 analysis_item 에 보존).

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

## 콘솔 도메인 표면 — DB 전환 완료 (ALPHA-513 → 도메인별 실전환)

tenant-console-ui 도메인 계약(repository.real.ts)과 1:1 인 화면 표면은 **전 도메인이
실 원장으로 전환됐다** — 마지막 explanations 쓰기 실전환(ALPHA-613)의
`ExplanationMockStore` 삭제로 콘솔 mock 패키지가 소멸했다(mock 표면 0).
explanations(가격 변동 설명)는 ALPHA-607(읽기 실조회)·613(쓰기 원장 전이·감사)으로
전환됐다 — 조회는 현황판, 쓰기는 사후 운영(최종 문구 정정·검수 이관·제공 중단 — 사유
필수)이고 판정 게이트(승인·반려)는 Review Queue 소관이다(역할 분담, 2026-07-29).
scope(시장·종목 제공 범위)는 ALPHA-606 으로 serving_scope(옵트아웃 토글)·analysis_item
(종목 유니버스) 실조회/전이로 전환됐다 — 행 부재 = 기본 제공, 시장 커버리지 토글은
TA·종목 제외 토글은 CR 전용이다(serving_scope.scope_type 경계와 1:1). MVP 는 국내
상장 ETF 한정(ADR-0024)이라 시장은 KRX 하나이고 serving_scope 엔 MIC(XKRX)로 저장된다.
screening(금칙어·기준·면책 문구)은 ALPHA-438 로 policy_version·screening_rule 실
writer 로 전환됐다 — 모든 변경이 불변 버전 발행(ADR-0018)이고 쓰기는 CR 전용,
온보딩 기본은 자동 제공 ON 이다. members(사용자 관리)는 ALPHA-119 로 member 원장
실데이터로 전환됐고(등록·목록·비활성화 + 역할 변경 ALPHA-499), session 은
ALPHA-500 으로 실전환됐다 — name 은 인증 주체(member 원장), 테넌트 컨텍스트는
배포 설정(`console.tenant.*`, 온프렘 박스=테넌트 1:1)이 소스다. dashboard(제공
트래픽 KPI)는 ALPHA-128 부터 serving_request_metric 집계 실데이터다(mock 단계 없음).

- **응답 원천은 실 원장** — service 가 repository 로 온프렘 DB 를 실조회·전이한다.
  DB 연동은 도메인 단위로 service 의 mock 스토어 의존을 repository 로 교체하며
  진행했고(UI 는 계약 불변이라 무변경), 마지막 explanations 전환으로 `mock` 패키지가
  사라졌다.
- **와이어 타입은 `dto` 패키지** — 요청·응답 계약은 `dto` 의 `XxxRequest`/
  `XxxResponse` record 이고, 컨트롤러가 `XxxResponse.from(스토어/도메인 record)` 로
  매핑해 반환한다(서비스는 여전히 mock/도메인 record 반환). mock record(스토어 형)와
  형식이 같아도 별도 타입이다 — DB 연동 시 `from()` 의 매핑원이 mock record 에서
  repository record 로 바뀐다. 네이밍(`Xxx{Request,Response}`, `Dto` 접미사 없음)은
  tenant-sync-api·publication-api·super-admin-api(ALPHA-523) dto 규약을 따른다.
- **JSON 은 camelCase** — UI 타입이 계약의 SSOT 라 기존 검수 표면(snake_case)과
  다르다. `final` 은 Java 예약어라 컴포넌트명은 `finalText`, JSON 은 `@JsonProperty`.
- **성공·에러 모두 공통 응답 포맷(`ApiResponse`)** — 콘솔 전 표면이 jvm-common 봉투
  `{isSuccess,code,message,result}` 로 내려간다. 계약 DTO 는 `result` 안에 있고(검수·인증
  표면은 snake_case·ALPHA-513 표면은 camelCase — 네이밍은 위 규약대로), 뮤테이션도 200 +
  `result` 생략이다(204 는 쓰지 않는다). 성공까지 봉투로 감싸는 건 콘솔 계열 API 규약이다
  — tenant-console-api·super-admin-api 가 채택했다(ALPHA-521·522). 실계약 조회 표면
  (tenant-sync-api·publication-api)은 raw DTO 성공을 유지하는 의도적 분기다(AGENTS Rule 7·11).
- **인가는 permission-matrix.md 역할을 강제** — mock 단계의 한시 예외(인증만 강제)는
  전 도메인 DB 전환으로 해제됐다. ConsoleAuthFilter 가 매 요청 원장 role 로 라우트별
  권한을 판정한다(fail-closed).

## 스텁 → 실구현 교체 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
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

테스트 131건 — 검수 계약(승인·수정 승인=전이+스냅샷 게시+기록+감사, 반려·차단
사유 필수, 편집 누락·오타 400 강등 차단, 409 수렴), 인증
계약(로그인 성공/실패 동일 코드·SSO 전용 거부, 필터 401/403·역할 강제·matrix
parameter 우회 차단·매핑 부재 fail-closed·세션 주체=원장 정체성 SSOT, 부트스트랩
멱등·해시 저장), 사용자 관리 계약(등록 검증·중복 409·마지막 관리자 409, 역할
변경의 자기변경 403·조건부 갱신 409·감사 기록), 세션 계약(주체 이름·설정 테넌트
컨텍스트·프로필 원장 기록·길이 상한 400), 제공 범위 계약(serving_scope 옵트아웃
upsert·시장 MIC 저장·유니버스 조회·시장 토글=TA·종목 토글=CR), 콘솔 mock 표면의
UI 계약(camelCase·`final` 필드·상태 전이·어휘 게이트·404)을 인코딩한다.
단위 테스트는 리포지토리(좁은 인터페이스)를 페이크로 스텁해 DB 없이 돈다. DB 계약은
Testcontainers Postgres + Flyway(migrations-onprem) 통합 테스트가 검증한다 —
`contextLoads` 가 `ddl-auto=validate` 로 엔티티↔실스키마 정합을, `ReviewMemberRepositoryIT`
가 decide 가드·publish ON CONFLICT·활성 조회·save 를, `ScopeIT` 가 serving_scope 옵트아웃
upsert·MIC 저장·유니버스 조회를 실 쿼리로 확인한다(Docker 없으면
JUnit `@EnabledIf` 로 disabled 로 보고 — 숨겨진 통과가 아니다; CI/Docker 에서 실행).
