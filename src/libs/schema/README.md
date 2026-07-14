# libs/schema

DB 스키마 **단일 진실 공급원(SSOT)** 모듈. 공유 DB가 서비스 간 계약이므로(ADR-0005,
[docs/implementation.md](../../../docs/implementation.md) §4) 모든 마이그레이션을 여기서 관리한다.

이 모듈은 **Spring 앱이 아니다.** Flyway 마이그레이션을 실행하기만 하는 전용 Gradle 모듈이다
(`application.yml` 없음, JPA 없음). 설정은 전부 `build.gradle`(Groovy)의 Flyway 플러그인 블록에 있다.

## 로컬 DB — Docker Postgres

로컬 검증은 **Docker Postgres**(host port `55432`)를 쓴다. 기존 로컬 PostgreSQL은 기본 대상이 아니다.
컨테이너는 repo 루트의 `docker-compose.schema.yml`로 띄운다.

```bash
# repo 루트에서
docker compose -f docker-compose.schema.yml up -d
```

## 마이그레이션 실행

> Gradle 명령은 JVM 루트인 `src/`에서 실행한다(`./gradlew`가 거기 있다).

```bash
cd src
./gradlew :libs:schema:flywayInfo       # 마이그레이션 상태 확인
./gradlew :libs:schema:flywayMigrate    # 적용
./gradlew :libs:schema:flywayValidate   # 적용본 검증
```

기본 접속값은 Docker Compose와 일치한다: `jdbc:postgresql://localhost:55432/edge`, user/pw `edge`/`edge`. 객체는 기본 `public` 스키마에 둔다.

## 다른 DB로 override

접속값은 `-P` Gradle property나 환경변수로 override할 수 있다(우선순위: property → env → 기본값).
단, repo에 체크인된 `gradle.properties`에 `flyway.*`(및 `systemProp.flyway.*`)를 두는 것은 CI가 거부한다
(`schema-validate` guard). 체크인된 설정이 배포/검증 대상 DB나 마이그레이션 동작을 바꾸지 못하게 하기 위함이다.

Gradle property로:

```bash
./gradlew :libs:schema:flywayMigrate \
  -Pflyway.url=jdbc:postgresql://localhost:55432/edge \
  -Pflyway.user=edge \
  -Pflyway.password=edge
```

환경변수로:

```bash
FLYWAY_URL=jdbc:postgresql://localhost:55432/edge \
FLYWAY_USER=edge \
FLYWAY_PASSWORD=edge \
./gradlew :libs:schema:flywayMigrate
```

## 실행 주체 — 앱이 아니라 파이프라인이 Flyway를 실행한다

DB 스키마 변경은 **배포 파이프라인**에서만 일어난다. 위 로컬 명령은 개발자 검증용이고,
공유 DB(dev/staging·prod)를 바꾸는 주체는 각 JVM/Spring 앱이 아니라 CI/CD다.

- **merge 자체는 DB를 바꾸지 않는다.** DB 변경은 파이프라인이 마이그레이션을 실행할 때만 일어난다.
- **dev 머지** → dev DB 마이그레이션 대상 (`.github/workflows/schema-migrate.yml`, **`src/libs/schema/` 변경 커밋에서만** 실행).
- **PR(→dev/main)** → ephemeral Postgres에 `:libs:schema:flywayMigrate`+`flywayValidate` 실제 적용·검증만 한다 (`.github/workflows/schema-validate.yml`). secret을 쓰지 않고 운영 DB는 건드리지 않는다.
- **prod 마이그레이션(main 머지 → prod DB, 승인 게이트 후)** 은 목표 토폴로지지만 아직 워크플로가 없다. prod 인프라(RDS·클러스터·`production` environment)가 생기면 별도 티켓에서 dev와 같은 패턴으로 재도입한다(현재는 dev 인프라만 존재).
- **앱 배포(API CD)는 마이그레이션 CD와 분리돼 있다** — 앱 워크플로는 CI에서 `schema-migrate` 완료를 기다리지 않는다. "스키마가 코드보다 먼저"는 확장-수축 + PR 순서 규율로 지킨다: **확장 마이그레이션 PR을 먼저 머지·적용(schema-migrate 초록 확인)한 뒤 의존 코드 PR을 머지한다**(docs/implementation.md §4).

### 배포 시 마이그레이션은 VPC 내부 ECS one-off task에서 실행한다

운영/스테이징 RDS는 private 서브넷 + SG 제한이라 **GitHub-hosted 러너(VPC 밖)에서 직접 접속할 수 없다.**
그래서 배포 워크플로는 러너에서 Flyway를 돌리지 않는다. 대신:

1. 러너가 **OIDC로 AWS 인증**(장기 액세스 키 없음).
2. 이 커밋의 SQL을 담은 **마이그레이션 이미지**(`src/libs/schema/Dockerfile`, Flyway CLI + SQL)를 ECR에 push.
3. **ECS Fargate one-off task**(`infra/terraform/modules/schema-migrate`)를 private 서브넷에서 RunTask로 실행 → Flyway가 VPC 안에서 RDS에 적용.
4. task 완료(STOPPED)까지 대기 후 컨테이너 exit code로 성공/실패 판정.

- **DB 비밀번호는 RDS 관리형 Secrets Manager 시크릿**에서 task로 주입한다(코드·로그·GitHub에 평문 없음). URL/user는 RDS 출력(평문 env). 별도 `FLYWAY_*` secret을 만들지 않는다.
- 이미지 안 `flyway.conf`의 정책 플래그(`baselineOnMigrate`·`cleanDisabled`·`validateMigrationNaming`)는 `build.gradle`의 `flyway{}` 블록과 **동일하게 유지**한다.

### CI 운영 설정 (1회)

1. **Terraform apply** (`infra/terraform/envs/dev`) — schema-migrate task·ECR·SG·OIDC 배포 역할을 생성한다. 계정에 GitHub OIDC provider가 이미 있으면 `create_github_oidc_provider=false` + `github_oidc_provider_arn=<기존 ARN>`로 참조한다. (배포 role은 `dev` **브랜치**에서 도는 워크플로만 신뢰한다 — OIDC sub가 `...:ref:refs/heads/dev`이며 모듈의 `github_branch_refs`로 핀한다. 워크플로는 `environment:`를 쓰지 않는다 — Free+private 플랜은 environment 배포 브랜치 정책을 강제할 수 없어 브랜치 핀이 안 되기 때문. ALPHA-313.)
2. **repo-level 변수(vars) 등록** — `terraform output` 값을 GitHub **repository variables**(secret 아님, 식별자)로 넣는다. environment 를 쓰지 않으므로 environment vars 가 아니라 repo vars 다:
   - `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`(=`gha_deploy_role_arn`), `ECS_CLUSTER_ARN`, `MIGRATE_TASK_FAMILY`, `MIGRATE_ECR_REPOSITORY`, `MIGRATE_SUBNET_IDS`, `MIGRATE_SECURITY_GROUP_ID`, `MIGRATE_LOG_GROUP`.
   - DB 접속 비밀번호는 여기 없다(RDS 시크릿에서 task가 직접 읽음).
3. **prod (후속)**: prod 인프라가 아직 없어 prod 마이그레이션 워크플로는 두지 않는다. prod env terraform(선행 티켓) 적용 후, 배포 role 을 `refs/heads/main` 으로 핀하고 동일 vars 를 채운 뒤 dev 와 같은 패턴의 prod 워크플로를 재도입한다. 승인 게이트가 필요하면 Pro 플랜의 environment protection(Required reviewers)을 쓴다.
- (권장) **Branch protection**: `dev`와 `main` 모두에 `schema-validate` 상태 체크(job: `migrate-and-validate`)를 required로 지정한다. 릴리스 경계가 `dev -> main`이므로 main에도 필요하다. (현재 private + free 플랜이라 branch protection 불가 — 그래서 워크플로에 `paths` 필터를 두고 있다. required로 지정하는 시점에는 필터를 걷어내야 한다: path로 스킵된 required check는 Pending으로 남아 PR을 영구 차단한다. `schema-validate.yml` 상단 주석 참고.)
  - 함께 **"Require branches to be up to date before merging"** 를 켠다. 버전 단조성 guard는 체크 실행 시점의 base tip 기준이라, 이 설정이 없으면 같은 base에서 갈라진 두 PR이 각각 통과 후 순서대로 머지될 때 낮은 버전 마이그레이션이 뒤늦게 착지해 배포 `flywayMigrate`가 out-of-order로 실패할 수 있다. 머지 전 최신 base로 rebase를 강제하면 guard가 최신 base_max로 재검증한다.

### JVM/Spring 앱은 schema **consumer**다

앱은 스키마를 소비만 하고, 마이그레이션하지 않는다. `libs/schema`와 파이프라인이 유일한 schema **migrator**다.

- 앱에 Flyway 의존성·마이그레이션 SQL을 두지 않는다(마이그레이션은 이 모듈에만 있다).
- Spring Boot 앱은 기동 시 마이그레이션을 실행하지 않는다 — `spring.flyway.enabled=false`.
- Hibernate/JPA는 스키마를 생성/변경하지 않는다 — `spring.jpa.hibernate.ddl-auto`는 `create`/`update` 금지, 필요 시 `validate`만 허용.

## 마이그레이션 파일

- 위치: `migrations/`
- 파일명: timestamp 버전 — `VyyyyMMddHHmm__description.sql` (공유 DB 동시 작업 시 버전 충돌 방지)
- **이미 적용된 마이그레이션 파일은 수정하지 않는다.** 변경은 항상 새 `V...sql`로 추가한다(확장-수축, docs/implementation.md §4).

## 정책 (고정)

- `baselineOnMigrate = false` — 그린필드 DB. 기존 DB adoption이 아니다.
- `cleanDisabled = true` — `flyway clean` 금지(데이터 전체 삭제 방지).
- `validateMigrationNaming = true` — 파일명 규칙 위반 시 실패.
