# libs/schema

DB 스키마 **단일 진실 공급원(SSOT)** 모듈. 공유 DB가 서비스 간 계약이므로(ADR-0005,
[schema.md](../../../docs/schema.md)) 모든 마이그레이션을 여기서 관리한다.

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
- **dev 머지** → dev/staging DB 마이그레이션 대상 (`.github/workflows/deploy-dev.yml`).
- **main 머지** → prod DB 마이그레이션 대상 (`.github/workflows/deploy-prod.yml`, 승인 게이트 후).
- **PR(→dev/main)** → ephemeral Postgres에 `:libs:schema:flywayMigrate`+`flywayValidate` 실제 적용·검증만 한다 (`.github/workflows/schema-validate.yml`). secret을 쓰지 않고 운영 DB는 건드리지 않는다.

### 배포 시 마이그레이션은 VPC 내부 ECS one-off task에서 실행한다

운영/스테이징 RDS는 private 서브넷 + SG 제한이라 **GitHub-hosted 러너(VPC 밖)에서 직접 접속할 수 없다.**
그래서 배포 워크플로는 러너에서 Flyway를 돌리지 않는다. 대신:

1. 러너가 **OIDC로 AWS 인증**(장기 액세스 키 없음).
2. 이 커밋의 SQL을 담은 **마이그레이션 이미지**(`src/libs/schema/Dockerfile`, Flyway CLI + SQL)를 ECR에 push.
3. **ECS Fargate one-off task**(`infra/terraform/modules/schema-migrate`)를 private 서브넷에서 RunTask로 실행 → Flyway가 VPC 안에서 RDS에 적용.
4. task 완료(STOPPED)까지 대기 후 컨테이너 exit code로 성공/실패 판정(실패 시 앱 배포 진행 안 됨).

- **DB 비밀번호는 RDS 관리형 Secrets Manager 시크릿**에서 task로 주입한다(코드·로그·GitHub에 평문 없음). URL/user는 RDS 출력(평문 env). 별도 `FLYWAY_*` secret을 만들지 않는다.
- 이미지 안 `flyway.conf`의 정책 플래그(`baselineOnMigrate`·`cleanDisabled`·`validateMigrationNaming`)는 `build.gradle`의 `flyway{}` 블록과 **동일하게 유지**한다.

### CI 운영 설정 (1회)

1. **Terraform apply** (`infra/terraform/envs/dev`) — schema-migrate task·ECR·SG·OIDC 배포 역할을 생성한다. 계정에 GitHub OIDC provider가 이미 있으면 `create_github_oidc_provider=false`로 두고 기존 provider를 참조한다.
2. **Environments 생성**: `development`, `production`.
3. **environment 변수(vars) 등록** — `terraform output` 값을 GitHub `development` environment의 **variables**(secret 아님, 식별자)로 넣는다:
   - `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`(=`gha_deploy_role_arn`), `ECS_CLUSTER_ARN`, `MIGRATE_TASK_FAMILY`, `MIGRATE_ECR_REPOSITORY`, `MIGRATE_SUBNET_IDS`, `MIGRATE_SECURITY_GROUP_ID`, `MIGRATE_LOG_GROUP`.
   - DB 접속 비밀번호는 여기 없다(RDS 시크릿에서 task가 직접 읽음).
4. **prod 승인 게이트**: `production` environment에 **Required reviewers**를 지정한다(배포 브랜치를 `main`으로 제한). prod는 별도 prod env terraform(선행 티켓) 적용 후 동일한 vars를 `production` environment에 채운다.
- (권장) **Branch protection**: `dev`와 `main` 모두에 `schema-validate` 상태 체크(job: `migrate-and-validate`)를 required로 지정한다. 릴리스 경계가 `dev -> main`이므로 main에도 필요하다.
  - 함께 **"Require branches to be up to date before merging"** 를 켠다. 버전 단조성 guard는 체크 실행 시점의 base tip 기준이라, 이 설정이 없으면 같은 base에서 갈라진 두 PR이 각각 통과 후 순서대로 머지될 때 낮은 버전 마이그레이션이 뒤늦게 착지해 배포 `flywayMigrate`가 out-of-order로 실패할 수 있다. 머지 전 최신 base로 rebase를 강제하면 guard가 최신 base_max로 재검증한다.

### JVM/Spring 앱은 schema **consumer**다

앱은 스키마를 소비만 하고, 마이그레이션하지 않는다. `libs/schema`와 파이프라인이 유일한 schema **migrator**다.

- 앱에 Flyway 의존성·마이그레이션 SQL을 두지 않는다(마이그레이션은 이 모듈에만 있다).
- Spring Boot 앱은 기동 시 마이그레이션을 실행하지 않는다 — `spring.flyway.enabled=false`.
- Hibernate/JPA는 스키마를 생성/변경하지 않는다 — `spring.jpa.hibernate.ddl-auto`는 `create`/`update` 금지, 필요 시 `validate`만 허용.

## 마이그레이션 파일

- 위치: `migrations/`
- 파일명: timestamp 버전 — `VyyyyMMddHHmm__description.sql` (공유 DB 동시 작업 시 버전 충돌 방지)
- **이미 적용된 마이그레이션 파일은 수정하지 않는다.** 변경은 항상 새 `V...sql`로 추가한다(확장-수축, schema.md §2).

## 정책 (고정)

- `baselineOnMigrate = false` — 그린필드 DB. 기존 DB adoption이 아니다.
- `cleanDisabled = true` — `flyway clean` 금지(데이터 전체 삭제 방지).
- `validateMigrationNaming = true` — 파일명 규칙 위반 시 실패.
