// DB 스키마 SSOT 모듈 — Flyway 전용. Spring 앱이 아니다.
// PostgreSQL 드라이버 + flyway-database-postgresql 모듈을 Flyway Gradle 플러그인 classpath에 올린다.
buildscript {
    repositories { mavenCentral() }
    dependencies {
        classpath("org.postgresql:postgresql:42.7.4")
        classpath("org.flywaydb:flyway-database-postgresql:10.21.0")
    }
}

plugins {
    id("org.flywaydb.flyway") version "10.21.0"
}

// 접속값 우선순위: 환경변수 → Gradle property → 기본값(Docker Compose와 일치).
// CI/배포는 secrets를 env로 주입하므로 env를 최우선으로 둔다. 이렇게 하면 repo에 체크인된
// gradle.properties의 flyway.* 가 배포/검증 대상 DB를 가로채지 못한다(secrets override 불가).
// 로컬 -P 오버라이드는 env가 없을 때 그대로 동작한다.
fun cfg(prop: String, env: String, default: String): String =
    providers.environmentVariable(env)
        .orElse(providers.gradleProperty(prop))
        .orElse(default)
        .get()

flyway {
    url = cfg("flyway.url", "FLYWAY_URL", "jdbc:postgresql://localhost:55432/edge")
    user = cfg("flyway.user", "FLYWAY_USER", "edge")
    password = cfg("flyway.password", "FLYWAY_PASSWORD", "edge")

    locations = arrayOf("filesystem:${projectDir}/migrations")

    baselineOnMigrate = false
    cleanDisabled = true
    validateMigrationNaming = true
}
