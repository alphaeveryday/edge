# 구현 결정사항 (확정)

각 절은 "현행이 무엇인가"를 서술한다. "왜 그렇게 결정했나"는 대응 [ADR](adr/)에 있다.

## 1. 코드베이스

> 결정 배경: [ADR-0016](adr/0016-single-repo-two-artifacts.md), [ADR-0011](adr/0011-rls-to-physical-isolation.md)

- **단일 레포 유지**, 배포 아티팩트 2종으로 분리: `edge-cloud` (super-admin, tenant-sync-api, pipeline 연동) / `edge-onprem` (sync-agent, intake, screening-worker, tenant-console, publication-api). sync-agent=DMZ Pull·검증, intake=내부망 수신·저장([adr/0036](adr/0036-sync-agent-intake-topology.md); 단일 모듈 옵션에서는 합침). **위젯 UI는 이 두 런타임 아티팩트와 별개인 정적 빌드 산출물**로 납품된다(컨테이너·compose 서비스가 아님, 증권사가 자기 환경에서 임베드·호스팅 — [adr/0035](adr/0035-widget-ui-build-artifact.md)).
- 기존 Gradle 멀티모듈에서 **widget 모듈 삭제**, tenant-console은 onprem 아티팩트로 이동.
- **shared-tenancy(RLS) 모듈 삭제.** On-Prem이 테넌트별 물리 격리이므로 RLS의 존재 이유가 소멸. "RLS 설계 → 물리 격리 전환" 의사결정 자체는 기술 스토리로 문서화해 보존 ([ADR-0011](adr/0011-rls-to-physical-isolation.md)).
- Flyway 중앙화(shared-migration)는 유지하되 cloud/onprem 마이그레이션 세트를 분리.

## 2. 데모/개발 토폴로지 (8월 중간평가, 11월 데모데이)

> 결정 배경: [ADR-0017](adr/0017-demo-topology-compose.md)

- 가상 온프렘 = **별도 EC2 1대 + Docker Compose**로 온프렘 스택 전체 구동 (Publication API, Screening Worker, Tenant Console, Sync Agent, Intake, PostgreSQL, Redis).
- 딜리버리 스토리: "증권사 서버에 Compose 파일 하나로 설치된다."
- On-Prem 스택: **PostgreSQL + Redis 유지**.
- Cloud 측은 기존 AWS 구조(ECS, Step Functions, RDS, 3-layer subnet) 유지하되 serving cluster 구성을 신규 컴포넌트에 맞게 개정.

## 3. Compliance Rule 배포 경로

> 결정 배경: [ADR-0018](adr/0018-rule-deployment-path.md)

- **Rule Type = 코드, 소프트웨어 릴리스(온프렘 버전 업그레이드)로만 배포.** Sync 채널은 데이터(이벤트/설명 후보) 전용 — 룰 정의를 Sync로 내려보내지 않는다 (원격 코드 배포로 간주될 소지 차단).
- **Rule Instance = 증권사가 Tenant Console에서 설정** (금칙어 목록, 임계값, 처리 기준). 정책 버전은 불변(immutable), 정책 변경 이력은 Audit Log에 기록.

## 4. DB 변경 절차

> 구 docs/schema.md에서 흡수 — 피벗과 무관하게 유효한 현행 프로세스. 결정 배경: [ADR-0005](adr/0005-db-as-contract.md).
> 구 문서의 테이블 레지스트리(widget-api 등 피벗 전 서비스 기준)는 폐기했다 — 신규 도메인 엔티티는 [domain/state-machine.md](domain/state-machine.md)의 ERD가 기준이며, 실제 테이블 확정 시 레지스트리를 재구축한다.

**소유권 원칙**
- 모든 테이블은 **단일 쓰기 소유자(writer)** 를 갖는다. 그 서비스만 INSERT/UPDATE/DELETE 한다.
- 읽기는 여러 서비스가 할 수 있다(reader). 소유자가 아닌 서비스의 쓰기는 금지.
- 소유자는 그 테이블의 컬럼 변경에 책임을 진다 — 확장-수축 절차를 주도하고, 다른 reader가 깨지지 않도록 보장한다.

**확장-수축(expand-contract) 절차 — 왜 필요한가**
머지 ≠ 배포다. PR이 머지돼도 서비스 롤아웃에는 시차가 있어, 한순간 **구버전 코드와 신버전 코드가 같은 DB**를
동시에 바라본다. 그래서 스키마를 한 번에 파괴적으로 바꾸면(컬럼 rename/drop) 롤아웃 도중 한쪽이 깨진다.
변경을 **후방 호환되는 단계**로 쪼개 이 시차를 견딘다.

**3단계**

1. **확장(expand)** — 새 컬럼/테이블을 **추가만** 한다. 기존 구조는 그대로 두어 구버전 코드가 계속 동작한다. (NOT NULL은 기본값과 함께, 또는 일단 nullable로.)
2. **전환(migrate)** — 모든 쓰기/읽기 코드를 새 구조로 옮긴다. 필요하면 백필(backfill)로 기존 데이터를 채운다. **모든 서비스의 배포가 끝날 때까지** 기다린다.
3. **수축(contract)** — 더 이상 아무도 참조하지 않는 옛 컬럼/테이블을 제거한다.

**변경 유형별 적용**

| 변경 | 절차 |
|---|---|
| 컬럼 추가 | 보통 확장 1단계로 충분(nullable/기본값). |
| 컬럼 이름 변경 | 확장(새 컬럼 추가) → 전환(양쪽 쓰기→읽기 이전, 백필) → 수축(옛 컬럼 제거). **직접 rename 금지.** |
| 컬럼 삭제 | 전환(모든 참조 제거) → 수축(컬럼 제거). |
| 타입 변경 | 이름 변경과 동일하게 새 컬럼으로 우회. |

**PR·배포 매핑**
- 각 단계는 **별도 PR**이다(확장 / 전환 / 수축). 한 PR에 몰지 않는다.
- 코드 변경 자체는 PR 단위로 원자적이되, **DB 계약의 단계 전환 사이에는 배포 완료를 기다린다.**
- 배포 순서: **확장 마이그레이션을 코드 배포보다 먼저** 적용한다(새 컬럼이 있어야 새 코드가 동작).
  이 순서는 **CI가 강제하지 않는다** — 마이그레이션 CD(`schema-migrate`)와 앱 CD(`deploy-app`)는 분리돼 있어 앱 배포가 migrate를 기다리지 않는다. 확장 마이그레이션 PR을 먼저 머지해 `schema-migrate`가 초록인 것을 확인한 뒤 의존 코드 PR을 머지하는 것은 작성자 책임이다.

**변경 체크리스트** — 스키마를 바꿀 때:
- [ ] `libs/schema/migrations-cloud/`에 마이그레이션 추가(Flyway, timestamp 버전 `VyyyyMMddHHmm__`).
- [ ] (생성기 도입 후) `libs/schema/generated/` 모델 재생성.
- [ ] 마이그레이션과 생성 모델을 **같은 PR/커밋**으로 함께 올린다([ADR-0005](adr/0005-db-as-contract.md)).
- [ ] 이 변경이 확장-수축 중 **어느 단계인지** PR 설명에 명시한다.
- [ ] 리뷰: `libs/schema`는 JVM·Python 양쪽 소비자가 영향을 받으므로 **양쪽 리뷰**를 받는다(CODEOWNERS로 강제 예정).

**generated 모델 재생성**
> **현황:** generated 모델 **생성기가 아직 없다.** ADR-0005·README의 "스키마 변경 시 generated
> 동반 커밋" 규칙은 **그대로 유효하다.** 다만 그 **전제인 생성기가 아직 없어 현재 생성할 산출물이 없고**
> (`generated/`는 비어 있음), 그 전까지는 `libs/schema/migrations-cloud/`의 Flyway SQL이 사실상 계약을 정의한다.
> 생성기는 별도 후속 티켓에서 도입하며, 도입되는 순간 아래 규칙이 그대로 적용된다(규칙 자체를 보류·완화하지 않는다).

생성기 도입 이후의 규칙:
- `generated/`는 **손으로 고치지 않는다.** 항상 `schema`(마이그레이션/정의)로부터 생성한다.
- 재생성은 스키마 변경과 **동일 PR**에 포함한다 — 정의와 모델이 어긋난 채 머지되면 안 된다.
- 여러 런타임(JVM·Python)이 같은 정의에서 모델을 생성해 **동일한 계약**을 공유하도록 보장한다.

## 5. 현행 CD (지속적 배포)

> 구 docs/architecture.md에서 흡수 — 현행 운영 사실. 배포 대상 앱 구성은 피벗([context.md](context.md)의 서비스/API 변경표)에 따라 재편됐다(widget·gateway 제거 — ADR-0010·0032).

dev 는 GitHub Actions 로 구현됐다: 스키마(`src/libs/schema/**`) 변경이 dev 에 머지되면 `schema-migrate.yml` 이 실 dev RDS 에 마이그레이션을 적용한다. 백엔드 앱별 워크플로(`deploy-<app>.yml`, 2종 super-admin-api·tenant-sync-api — tenant-console-api 는 onprem 플레인이라 dev ECS·CD 에서 제거)는 자기 path 변경에 트리거되는 독립 배포다(ECR semver 이미지 → ECS 롤링). `data-pipeline` 은 `deploy-data-pipeline.yml` 로 raw 수집 배치 이미지를 ECR 에 push 한다. 프론트별 워크플로(`deploy-<ui>.yml`, 2종 tenant-console-ui·super-admin-ui)는 `deploy-ui.yml` 을 재사용해 pnpm 빌드 → S3 sync → CloudFront 무효화한다. 데모 온프렘 박스는 격리 스택이라 별개다 — `deploy-demo-onprem.yml`(수동 `workflow_dispatch`, 전용 배포 역할)이 이미지 빌드→SSM Run Command 로 박스 compose→MTS UI sync 를 한 번에 한다(dev 자동 CD 와 무관). 모두 마이그레이션 CD와 분리돼 있어 CI 에서 migrate 를 기다리지 않는다(순서는 확장-수축 + PR 순서 규율로 지킴 — 확장 마이그레이션 먼저 머지·적용 후 의존 코드). 인프라(`infra/terraform/envs/dev`) 자체도 CD 된다 — PR 은 `terraform-plan.yml`(read-only 역할)이 plan 을 PR 코멘트로 게시하고, dev 머지 시 `terraform-apply.yml`(apply 역할, trust 가 `ref:refs/heads/dev` 라 PR 은 assume 불가)이 apply 한다. bootstrap·foundation 스택은 수동. 원칙은 그대로다 — "전체 일괄 자동 배포"는 두지 않고, 마이그레이션 확장 단계가 코드 배포보다 먼저다. prod 배포는 prod 인프라 확정 후 같은 구조로 잇는다.
