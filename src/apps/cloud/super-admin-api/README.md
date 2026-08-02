# super-admin-api

벤더 운영자 콘솔(Cloud)의 백엔드 — 운영자 인증 + 콘솔 화면 표면(tenants·sources·analyses
는 DB, session 은 실세션 주체 투영). 화면·금지 항목의 SSOT 는
[docs/console-ia/super-admin-console.md](../../../../docs/console-ia/super-admin-console.md)이고,
이 README 는 이 모듈만의 비자명한 규율만 적는다. DB 는 tenants 도메인부터 배선됐다
(JPA·`ddl-auto=validate` — Flyway(libs/schema)가 DDL SSOT 라 Hibernate 는 검증만, ALPHA-526).
sources 는 운영 원장(`ops_*`)과 1분 원장(`minute_*`, 요약 관측 — 행 복제 아님)·analyses
읽기는 설명 원장(`explanation_*`) 읽기 전용 조회다(ALPHA-514·601·651). analyses 쓰기(정정·제외·복원)는 운영자 작업 원장(`admin_activity_log`) 전이다
(ALPHA-602). session 표면은 인증 세션 주체(SessionOperator) 투영으로 실전환됐다(ALPHA-608).
운영자 인증은 아직 in-memory(474).

## 지켜야 할 로컬 불변식

- **인증 게이트는 `AdminAuthFilter` 하나** — 미인증은 전 `/api/**` 표면 401
  (fail-closed, ALPHA-474 앱 방어선). 운영자는 단일 역할이라 역할 인가는 없다 —
  필터 `RULES` 는 표면 등록부다. 엔드포인트를 추가하면 `RULES` 에 행을 함께
  더한다 — 등록 없는 표면은 인증돼도 403 거부가 기본이라, 매핑을 빼먹으면 그
  API 는 배포돼도 닫혀 있다(우회가 아니라 누락이 안전한 쪽으로 실패).
- **경로 매칭은 정규화 후** — 필터는 context-path 를 제거하고 세그먼트별 matrix
  parameter(`;k=v`)를 벗긴 경로로 판정한다. MVC 의 PathPattern 매핑과 같은 기준을
  써야 `/api;x=y/...` 같은 우회로 필터가 통째로 건너뛰어지지 않는다.
- **콘솔 IA 금지 항목** — API Key 관리·테넌트 사용 중지/재개 표면을 만들지
  않는다(super-admin-console.md, epic ALPHA-424).

## 인증 (config 부트스트랩 운영자)

- **데모 경로(구현됨)**: `admin.auth.bootstrap-operators`(application.yaml) 계정을
  기동 시 BCrypt 해시로 들고, 이메일+비밀번호 로그인 → 세션(`SessionOperator`).
  세션 ID 재발급으로 고정(fixation) 차단, 실패 사유는 구분 없는 401 + 미존재
  계정에도 더미 BCrypt 비교(타이밍 오라클 차단). DB 미배선 단계라 시드 없이
  메모리 대조다 — tenant-console 의 DB 시드 방식과 그 지점만 다르다.
- **비밀번호는 env 주입만** — 이 API 는 공개 엣지(admin-api-dev ALB)라 온프렘
  내부망 전제의 tenant-console 과 달리 기본 비밀번호를 커밋하지 않는다.
  `ADMIN_BOOTSTRAP_OPERATOR_PASSWORD` 미주입이면 계정이 비활성으로 남아 로그인
  불가(fail-closed, 기동 시 warn). 로컬은 compose 가 `demo-operator-1` 을 주입한다.
  dev 배포는 Secrets Manager 시크릿(`edge-dev-super-admin-api/bootstrap-operator/password`,
  값은 TF 밖 수동 주입)을 ECS task secrets 로 배선한다(ALPHA-618).
- **세션 쿠키는 Secure 고정** — 공개 HTTPS 엣지 전제. 로컬(http)은 표준 Spring
  env 오버라이드 `SERVER_SERVLET_SESSION_COOKIE_SECURE=false` 로 끈다(compose 는
  설정돼 있음 — bootRun 직접 기동 시 수동 export, 안 끄면 브라우저가 쿠키를
  저장하지 않아 로그인이 조용히 풀린다). SameSite=Strict 는 경량 CSRF 방어다 —
  표준 CSRF 토큰은 ALPHA-474 에서 추가된다(tenant-console 과 같은 데모 범위).
- **운영 경로(설계)**: 운영자 IdP 연동(ALPHA-474) — 같은 `SessionOperator` 로
  수렴하는 별도 진입점. Spring Security 본격 도입도 그 시점.
- **의도적 생략(데모 범위)**: 로그인 레이트리밋·계정 잠금 없음 —
  `allowed_cidrs`·WAF(망 제한) 뒤의 운영자 표면 전제. CSRF 는 세션 쿠키
  SameSite=Strict·HttpOnly 로 경량 방어.

## 콘솔 화면 표면 (ALPHA-515 → 도메인별 DB 전환 중)

super-admin-ui 도메인 계약(repository.real.ts)과 1:1 인 화면 표면 4종 —
tenants(테넌트 목록·생성) · sources(데이터 소스 수집 상태·파이프라인 실행 이력) · analyses(가격 변동
분석 목록·정정·제외/복원) · session(운영자 컨텍스트·프로필).

- **응답 원천은 도메인별로 다르다** — **tenants 는 JPA**(`entity/Tenant`·`repository/
  TenantRepository`)로 실 `tenant` 테이블을 읽고 쓴다(ALPHA-526). **sources 는 운영 원장
  `ops_*` 읽기 전용 조회**(`repository/JdbcPipelineStatusRepository`, ALPHA-514)에 더해
  **1분 원장 `minute_*` 요약 관측**(`JdbcMinuteStatusRepository`, ALPHA-651 — 세션·창
  집계·무증거 파생, 행 복제 아님)이다 — 두 원장 모두 소유는 data-pipeline 이라(ADR-0005
  단일 writer) 여기선 **쓰지 않는다**. JPA 엔티티를 두지
  않는 이유: `ddl-auto=validate` 환경에서 소유하지 않은 5테이블에 이 앱 기동을 묶지 않기
  위함이다. **analyses 읽기는 설명 원장(`explanation_*`) 읽기 전용 조회**
  (`repository/JdbcAnalysisRepository`, ALPHA-601 — 소유는 analysis-engine, 같은 이유로 JPA
  없이 Jdbc). **analyses 쓰기(정정·제외·복원)는 운영자 작업 원장 전이**
  (`repository/JdbcAnalysisWriteRepository`, ALPHA-602) — explanation_result(analysis-engine
  소유)를 덮지 않고 super-admin-api **소유** 원장 `admin_activity_log` 에 작업자·사유·전후와 함께
  append 하며, 정정 본문·제외 여부는 읽기가 그 원장에서 오버레이한다(원본 불변, 단일 writer 규약
  유지). **session 은 인증 세션 주체(`SessionOperator`)를 투영해 반환한다**(ALPHA-608 — 별도
  저장소·테이블 없이 config 부트스트랩 계정이 로그인 시 세션에 실린 값). DB 연동은 이렇게 도메인
  단위로 service 의 스토어 의존을 repository·세션 주체로 교체하며 진행한다.

- **와이어 타입은 `dto` 패키지** — 요청·응답 계약은 `dto` 의 `XxxRequest`/
  `XxxResponse` record 이고, `XxxResponse.from(원천 record)` 로 매핑해 반환한다
  (tenants=JPA entity, sources=원장 조회 record, analyses=설명 원장 조회 record —
  service 가 변환, analyses 쓰기=mock record, session=세션 주체(`SessionOperator`) 투영을
  `AdminSessionService` 가 구성). 원천 형과 형식이
  같아도 와이어 형은 별도 타입이다 — tenants 는 `from()` 매핑원이 이미 JPA entity 다
  (admin·email·memo 는 원장 값(ALPHA-121 온보딩 기록), Sync 관측 필드(domain·
  lastSync·calls·bars)만 플레이스홀더 — 환경 어휘는 IA(PoC/Production, 구 표기는
  전환 기간 수용·레거시 DEV 는 Dev 표기)). 네이밍(`Xxx{Request,Response}`, `Dto` 접미사 없음)은 tenant-sync-api·
  publication-api dto 규약을 따른다 — mock 콘솔 모듈 중 첫 dto 패키지다(ALPHA-523).
- **JSON 은 camelCase** — UI 타입이 계약의 SSOT 다.
- **성공·에러 모두 공통 응답 포맷(`ApiResponse`)** — jvm-common 봉투
  `{isSuccess,code,message,result}` 로 내려간다. 계약 DTO 는 `result` 안의
  camelCase 이고, 뮤테이션도 200 + `result` 생략이다(204 는 쓰지 않는다).
  성공까지 봉투로 감싸는 건 콘솔 계열 API 규약이다 — super-admin-api·tenant-console-api
  가 채택했다(ALPHA-521·522). 실계약 조회 표면(tenant-sync-api·publication-api)은 raw
  DTO 성공을 유지하는 의도적 분기다(AGENTS Rule 7·11).
- **정정/무효화 사유 필수·감사 레코드**(콘솔 IA)는 쓰기 실전환(ALPHA-602)과 함께 계약에
  편입됐다 — 정정/제외는 사유 필수(빈 값 400), UI 가 사유를 받는다. 복원 사유는 선택.
  작업자·사유·변경 전후는 `admin_activity_log` 원장에 보존된다(열람 API 없음 — DB 보존).

## 스텁 → 실구현 교체 지점

| 클래스 (현재 상태) | 재작성 시점 | 재작성 내용 |
|---|---|---|
| (도메인별 mock 스토어 — 전환 완료) | — | tenants=JPA(ALPHA-526)·sources=원장 조회(ALPHA-514)·analyses 읽기=설명 원장 조회(ALPHA-601)·analyses 쓰기=운영자 작업 원장(ALPHA-602)·session=세션 주체 투영(ALPHA-608). `*MockStore` 전부 제거됨 |
| config 부트스트랩 운영자 | ALPHA-474 | Spring Security + 운영자 IdP 연동 |

## 실행·확인

```bash
# 루트에서 — compose 가 postgres·flyway(migrations-cloud)를 먼저 띄운다(depends_on).
# JPA(ddl-auto=validate)라 tenant 스키마가 있어야 기동한다. tenant 테이블은 미시드라
# GET /api/v1/tenants 는 생성 전까지 빈 목록이다.
docker compose up --build super-admin-api      # host 18082
curl localhost:18082/actuator/health
curl -i -X POST localhost:18082/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"operator@edge.local","password":"demo-operator-1"}'   # 200 + 세션 쿠키
```

```bash
# 테스트 포함 (src/ 에서). Testcontainers(Postgres) 통합 테스트가 있어 Docker 필요 —
# 컨테이너에 Flyway 로 migrations-cloud 를 적용해 Hibernate validate 로 엔티티↔스키마 검증.
./gradlew :apps:cloud:super-admin-api:build
```
