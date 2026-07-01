# libs/schema

DB 스키마 **단일 진실 공급원(SSOT)** 모듈. 공유 DB가 서비스 간 계약이므로(ADR-0005,
[schema.md](../../../docs/schema.md)) 모든 마이그레이션을 여기서 관리한다.

이 모듈은 **Spring 앱이 아니다.** Flyway 마이그레이션을 실행하기만 하는 전용 Gradle 모듈이다
(`application.yml` 없음, JPA 없음). 설정은 전부 `build.gradle.kts`의 Flyway 플러그인 블록에 있다.

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

- **merge 자체는 DB를 바꾸지 않는다.** DB 변경은 파이프라인이 `:libs:schema:flywayMigrate`를 실행할 때만 일어난다.
- **dev 머지** → dev/staging DB 마이그레이션 대상 (`.github/workflows/deploy-dev.yml`).
- **main 머지** → prod DB 마이그레이션 대상 (`.github/workflows/deploy-prod.yml`, 승인 게이트 후).
- **PR(→dev/main)** → ephemeral Postgres에 실제 적용·검증만 한다 (`.github/workflows/schema-validate.yml`). secret을 쓰지 않고 운영 DB는 건드리지 않는다.
- 접속값(`FLYWAY_URL/USER/PASSWORD`)은 코드가 아니라 CI secrets로 주입한다.
  - dev: `DEV_FLYWAY_URL` · `DEV_FLYWAY_USER` · `DEV_FLYWAY_PASSWORD`
  - prod: `PROD_FLYWAY_URL` · `PROD_FLYWAY_USER` · `PROD_FLYWAY_PASSWORD`

### CI 운영 설정 (repo Settings에서 1회)

워크플로가 동작하려면 아래를 GitHub repo 설정에 등록해야 한다(코드로는 불가).

- **Environments 생성**: `development`, `production`.
- **Secrets 등록**: 위 `DEV_FLYWAY_*`는 `development`, `PROD_FLYWAY_*`는 `production` environment에 넣는다.
- **prod 승인 게이트**: `production` environment에 **Required reviewers**를 지정한다. 그래야 `deploy-prod`의 migrate job이 운영 DB를 건드리기 전에 수동 승인을 거친다(필요 시 배포 브랜치를 `main`으로 제한).
- (권장) **Branch protection**: `dev`에 `schema-validate` 상태 체크를 required로 지정한다.

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
