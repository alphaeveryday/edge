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

## 마이그레이션 파일

- 위치: `migrations/`
- 파일명: timestamp 버전 — `VyyyyMMddHHmm__description.sql` (공유 DB 동시 작업 시 버전 충돌 방지)
- **이미 적용된 마이그레이션 파일은 수정하지 않는다.** 변경은 항상 새 `V...sql`로 추가한다(확장-수축, schema.md §2).

## 정책 (고정)

- `baselineOnMigrate = false` — 그린필드 DB. 기존 DB adoption이 아니다.
- `cleanDisabled = true` — `flyway clean` 금지(데이터 전체 삭제 방지).
- `validateMigrationNaming = true` — 파일명 규칙 위반 시 실패.
