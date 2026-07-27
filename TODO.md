# TODO — 하이브리드 피벗 실행 체크리스트

> 설계 지식은 [docs/](docs/)가 SSOT. 이 파일은 "다음에 무엇을 하나"만 담는 작업 목록이다.
> **항목은 항상 우선순위순으로 유지한다** — 추가할 때마다 전체를 재정렬하며 갱신하고, 완료 시 줄을 지운다.
> 항목 착수 시 Jira 티켓을 만든다(issue-first).

## 0. 하네스 소수정 잔여
- [ ] (하네스) edge-review 각도 보강 — CI/배포 워크플로 정확성 각도 신설(변경이 `.github/workflows/*deploy*`·배포 스크립트일 때: concurrency 부재·레지스트리/태그 미전달·`up -d` 등 fire-and-forget 명령의 무단언 성공). ALPHA-542 CD 리뷰에서 봇이 3건 잡고 로컬 게이트가 놓침 — H각도 신설과 같은 개선 패턴
- [ ] (하네스) ADR-0009의 `docs/proposals/` 죽은 링크 정리 — git 이력에도 존재한 적 없는 디렉토리
- [ ] (하네스) AGENTS.md 1행 `12-rule template` placeholder 제목 정리

## 1. 인터페이스 계약 확정 — 병렬 개발 블로커 (진기·영서 합의 세션 1회)
- [ ] **Event Bundle·Cloud Event Store 필드 스키마** 확정 → `docs/contracts/event-bundle-schema.md` 기입 (이벤트 타입별 필드·ID 체계·번들 JSON 구조·체크섬 대상 바이트)

## 2. PM/팀 확정·리스크 확인 (결정·조사 작업, 코딩 아님 — 8월 중간평가 기준 재정렬 2026-07-23)
- [ ] **데모 시나리오·시드 데이터 확정** — 8월 중간평가에서 보여줄 종목·이벤트·스토리 대본 + 데이터 공급 방식(로컬/데모에서 data-pipeline 실수집 vs 픽스처 주입). 가상 MTS 화면 범위·모니터링 최소치 등 "어디까지 구현" 결정들의 상위 입력
- [ ] **테넌트(증권사) 온보딩 절차 정의** — super-admin 테넌트 등록 → 인증서 발급([adr/0012](docs/adr/0012-sync-cert-bootstrap.md)) → 온프렘 설치 config 산출물 형식·secrets/인증서 주입 방식 → compose 기동의 end-to-end. 8월 기준 "프로비저닝 스크립트 1개" 수준이면 충분
- [ ] **외부 데이터 소스 약관 — 잔여 확정** — 1차 조사 완료(ALPHA-399, docs/domain/data-source-licensing.md). 잔여: 조건부/금지 소스의 법률 검토·공급자 공식 확인(실계약 전 필수 — 문서 스스로 잠정 스냅샷임을 명시)
- [ ] **GitHub 플랜/공개 여부 결정** — private+free 플랜이라 **branch protection 불가** (main 직접 push 방지·required check·CODEOWNERS 강제 전부 규율로만 유지 중, schema-validate.yml 주석 참조). Team 플랜 업그레이드 vs public 전환
- [ ] **OSS 라이선스 방침·인벤토리** — 온프렘 배포는 고객사 라이선스 실사 대상. GPL류 의존성 확인 + NOTICE 준비 + LICENSE 파일(proprietary 명시). 8월 데모에는 불요 — 실증권사 실사 시점 대비
- [ ] Exposure/Audit Log **보존 기간** (금융권 감사 요건) — 8월 데모에는 불요
- [ ] (하네스) AGENTS.md **Rule 6 토큰 예산**(4k/task·30k/session) 처분 — 집행 불가능한 사문 규칙이라 edge-review 인용 flag의 노이즈 원천. 삭제/현실화/유지 중 팀 결정

## 3. 품질 게이트·저장소 자동화 (MVP 구현 전 안전망)
- [ ] **PR CI 구축** — 변경 모듈만 path-filter로 빌드/테스트 (JVM `gradlew build` · Node `pnpm test` · Python `pytest`). 현재 PR에서 도는 건 schema-validate·terraform-plan뿐 — 일반 코드 테스트가 PR에서 안 돎
- [ ] **린팅/포맷팅 도입** — eslint+prettier(TS)·ruff(Python)·spotless(JVM), 3런타임 전부 미설정. **시점 주의: 코드 재편(§4) 후 도입하거나 포맷 전용 커밋으로 분리** — 재편 diff에 포맷 노이즈 섞이면 리뷰 오염

## 4. MVP 구현 (§1 계약 확정 후, docs/implementation.md 기준)
- [ ] 코드베이스 재편 마무리 — 아티팩트 2종 **빌드·compose 분리**(widget 삭제·onprem 매핑 선언은 완료. shared-tenancy(RLS)는 애초 미구현으로 확인 — 삭제 대상 없음. 데모 토폴로지·로컬 compose 항목과 연동)
- [ ] Flyway cloud/onprem 마이그레이션 세트 분리 + 도메인 물리 스키마(state-machine.md ERD 기준) 작성
- [ ] Walking skeleton: Tenant Sync API → Sync Agent → Raw Event Store → 상태 분기 1건 관통
- [ ] Screening Worker — 위험등급 산정 구현 잔여 (평가기 429·정정 동일 평가 430(ADR-0041)·SYSTEM 상태 이력 431 구현 완료 — **산정 주체 결정 2026-07-26: 온프렘 Screening Worker**, Cloud AI 는 가드레일 제공만. 등급 컬럼·번들 확장·maxRisk 소비가 산정 티켓 몫)
- [ ] Publication API — 요청/응답 스펙 정의(조회 단위·고객 해시 전달 위치) 후 구현 + Exposure Log 기록
- [ ] Tenant Console·Super Admin Console — console-ia/ 기준 재구축
- [ ] **fan-out 발번기** — analysis-engine 의 `explanation_result(DRAFT)`를 sync outbox `tenant_delivery` 로 승격하는 cloud 서비스. 이게 있어야 파이프라인 산출물이 자동으로 sync 경계를 넘어 데모까지 관통한다 — 현재는 수동 시드(`scratchpad/seed-cloud-demo.sql`)로 대체 중. **데모 토폴로지(EC2+compose 가상 온프렘)·MTS 화면·배포 CD(deploy-demo-onprem.yml)는 구축 완료**(ALPHA-533·444·445·542, 브라우저 관통 검증) — 남은 건 이 데이터 자동화뿐
- [ ] 로컬 개발 환경 정의 — cloud+onprem 동시 구동 compose

## 5. 문서·하네스 후속
- [ ] **테스트 전략 문서** — 모듈별 요구 테스트 층(단위/통합) 기준 + **Event Bundle 계약 테스트**(진기-영서 양단이 같은 스키마로 검증, §1 계약 확정과 짝)
- [ ] jvm-common ExceptionAdvice 공통 응답 포맷 계약 테스트 2건 — ① 500 응답 내부 메시지 비노출 ② 프레임워크 예외의 공통 포맷 변환(깔때기, Boot 업그레이드 시 회귀 감지). 예외 처리 일원화 시점에 의도적 유예(2026-07-23)
- [ ] **API 명세 2층 구조 확립** — 시맨틱 계약(멱등성·에러 의미·규칙)은 `docs/contracts/`(상위), 문법 명세(경로·필드·타입)는 모듈 코드 옆 기계가독 파일(tenant-sync-api `openapi.yaml`·Event Bundle JSON Schema — 위 계약 테스트의 "같은 스키마" 실체)로 두고 contracts/ 문서가 포인터로 가리킴. Publication API는 증권사 전달용 대외 산출물 — 처음부터 OpenAPI + writing-rules 톤 적용 (모듈 스캐폴드 시점에)
- [ ] **관측성·운영 표준 수립** — 구조화 로깅+상관 ID(이벤트/cursor 추적), **Sync 중단 장애 알림 기준**(Dashboard 알림의 입력), 온프렘에서 벤더가 로그를 못 보는 제약 하의 진단 설계, 백업/복구 절차(RDS + 온프렘 PostgreSQL). **8월 데모는 최소 부분집합만** — Sync 중단 알림 기준+상관 ID 우선, worker/sync-agent 주기는 결정 항목이 아니라 config 기본값으로 이때 함께 확정
- [ ] **Definition of Done 명문화** — 게이트(edge-review→docs-sync)+이슈 전환 기준을 README Git 컨벤션에 한 절로
- [ ] 온프렘 릴리스 절차 문서화 — Rule Type 배포가 "소프트웨어 릴리스"인데 버전 정책·업그레이드 방법 미정

## 6. 인프라
- [ ] **데모 온프렘 박스 하드닝** (ALPHA-445 코멘트에 상세) — ① sync mTLS 클라이언트 cert·인증서-테넌트 바인딩(현재 평문 HTTPS·`TenantResolver` 고정 1) ② compose 네트워크 세분화(ADR-0036 dmz/data/serving — 현재 단일망이라 sync-agent 가 DB 도달) ③ 박스 instance role ECR pull 스코핑(현재 `Resource=["*"]`). 데모는 동작하나 프로덕션급 신뢰경계엔 필요
- [ ] GitHub repo vars 수동 삭제 — `WIDGET_UI_BUCKET`·`WIDGET_UI_DISTRIBUTION_ID` (widget-ui CD 제거로 미사용)
- [ ] S3 gateway VPC endpoint 적용 — NAT 비용 절감 (**ALPHA-349**, 백로그)
