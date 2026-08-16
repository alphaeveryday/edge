# ADR-0038: 온프렘 조회 표준으로 JPA 도입 — 스키마는 Flyway SSOT, 앱은 validate-only

- 상태: 승인됨 — 가변 엔티티 예시 문면(ExposureLog·ExposureLogRecorder)은 대체됨 → [ADR-0053](0053-widget-direct-serving-no-personalization.md) (Exposure Log 은퇴, 2026-08-17; JPA·validate-only 표준 자체는 불변)
- 날짜: 2026-07-24

## 맥락
온프렘 JVM 모듈은 지금까지 DB 접근을 일관되게 **Spring `JdbcTemplate`** 로 해 왔다 — `@Entity`/`JpaRepository` 는 레포 전체에 하나도 없고, `tenant-console-api` 는 build.gradle 에 `// JPA 없이 JdbcTemplate(thin layered)` 을 명시해 둘 정도로 관례가 굳어 있었다. 조회 SQL 이 단순한 walking skeleton 국면에서는 이 선택이 합리적이었다.

그러나 조회 표면이 늘면서 raw SQL 반복(컬럼 나열·RowMapper·조인 문자열)과 타입 안전성 부재의 비용이 커진다. 특히 `publication-api` 의 서빙 조회는 `publication ⋈ analysis_item` 조인 + 상태 필터 + JSONB 근거 파싱으로, 앞으로 조건이 붙을수록 문자열 SQL 유지보수가 무거워진다.

동시에 이 프로젝트의 **스키마 SSOT 는 Flyway(`libs/schema`)** 이고 앱은 schema **consumer** 다(ADR-0005·`libs/schema/README`). JPA 를 들이더라도 이 경계는 절대 넘지 않아야 한다 — Hibernate 가 스키마를 생성/변경하면 SSOT 가 둘이 되어 확장-수축 규율(ADR-0005)이 무너진다.

## 결정
**온프렘 조회의 영속성 표준을 JPA(Spring Data JPA + Hibernate)로 전환한다. 그 시작으로 `publication-api` 를 JPA 로 옮긴다.** 단 아래 경계를 규약으로 고정한다.

1. **스키마는 건드리지 않는다 — `spring.jpa.hibernate.ddl-auto=validate`.** 앱 기동 시 Hibernate 는 매핑된 엔티티와 실제 스키마의 정합을 **검증만** 하고 DDL 을 발행하지 않는다. create/update 는 금지(`libs/schema/README` 규약 그대로). 마이그레이션은 여전히 `libs/schema` 와 파이프라인만의 책임이다(앱에 Flyway 런타임 의존성을 두지 않는다 — 테스트 제외).
2. **읽기 엔티티는 `@Immutable`.** 조회 전용 엔티티(`Publication`·`AnalysisItem`)는 Hibernate `@Immutable` 로 봉인해 이 경계로는 UPDATE/INSERT 가 나갈 수 없게 한다. 쓰기가 필요한 곳만 가변 엔티티(`ExposureLog`)로 둔다.
3. **필요한 테이블·컬럼만 매핑한다.** 모든 테이블을 엔티티화하지 않는다 — 모듈이 실제로 읽고 쓰는 테이블만 매핑한다. `validate` 는 매핑된 컬럼의 존재만 검사하므로 부분 매핑이 규약과 정합한다.
4. **얇은 레이어드 유지.** 리포지토리는 읽기 전용이면 `Repository` 마커를 상속해 save/delete 를 노출하지 않는다. 서비스가 쓰는 조회 모델(record)은 리포지토리 경계에서 매핑한다 — 엔티티를 상위 레이어로 흘리지 않는다(thin layered 규약).
5. **엔티티↔스키마 정합은 Testcontainers 로 검증한다.** 실 Postgres 컨테이너에 `migrations-onprem` 을 적용하고 `validate` 를 실통과시키는 통합 테스트를 둔다. H2 는 JSONB·IDENTITY 등 Postgres 문법을 지원하지 않아 이 검증을 대체할 수 없다.

## 대안
- **JdbcTemplate 유지** — 관례와 일치하고 의존성이 가볍지만, 조회 조건이 늘수록 raw SQL 반복·타입 부재 비용이 커진다. 표준을 바꾸는 것이 이 ADR 의 목적이므로 배제.
- **JPA + `ddl-auto=update`(또는 엔티티가 스키마 생성)** — 스키마 SSOT 가 Flyway 와 Hibernate 둘로 갈려 ADR-0005 확장-수축 규율이 붕괴한다. 절대 배제 — `validate` 만 허용.
- **전 온프렘 모듈 일괄 전환** — 한 번에 바꾸면 리스크가 크고 검증이 얕아진다. `publication-api` 를 파일럿으로 먼저 옮기고 표준을 확립한 뒤 확산한다.

## 결과
- `publication-api` 가 레포 최초의 JPA 모듈이 된다: `entity`(`Publication`·`AnalysisItem`·`ExposureLog`)·`repository`(`PublicationRepository`·`ExposureLogRepository`) 패키지 신설, `ExplanationStore`·`ExposureLogRecorder` 는 리포지토리를 감싸는 얇은 매퍼로 유지(계약·HTTP 형상 불변).
- **후속(범위 밖):** `tenant-console-api` 등 다른 온프렘 조회 모듈의 JdbcTemplate→JPA 전환과 그 build.gradle 의 `// JPA 없이 JdbcTemplate` 주석 정정은 별도 티켓에서 이 ADR 을 근거로 진행한다. 이 ADR 은 표준과 경계를 고정하고 파일럿(`publication-api`)만 전환한다.
- `libs/schema` 규약(ddl-auto validate-only)은 이 결정으로 앱 코드에 실제로 반영된다 — 문서로만 있던 "필요 시 validate" 가 첫 소비자를 얻는다.
