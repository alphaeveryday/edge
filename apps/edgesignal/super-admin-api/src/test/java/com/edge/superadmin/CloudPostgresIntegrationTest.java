package com.edge.superadmin;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * Cloud 스키마 정합을 실 Postgres 로 검증하는 통합 테스트의 공통 베이스(ALPHA-526).
 * 스키마 SSOT 는 Flyway(libs/schema)이므로 컨테이너에 migrations-cloud 를 적용한 뒤
 * Hibernate ddl-auto=validate 로 엔티티↔스키마를 대조한다(ADR-0005·ADR-0038). H2 는
 * IDENTITY·TIMESTAMPTZ 등 Postgres 문법을 대체하지 못해 이 검증을 대신할 수 없다.
 *
 * <p>컨테이너는 싱글턴 패턴으로 띄운다(static start·@Testcontainers 미사용) — 여러 테스트
 * 클래스가 캐시된 컨텍스트를 공유하므로, 클래스별로 컨테이너를 stop 하면 뒤 클래스가 죽은
 * 컨테이너에 붙어 연결 실패한다. Ryuk 이 JVM 종료 시 정리한다.
 *
 * <p>flyway.locations 의 절대 경로는 build.gradle 이 {@code cloud.migrations.dir} 시스템
 * 프로퍼티로 주입한다 — test 작업 디렉터리에 의존하지 않게 하기 위함.
 */
@SpringBootTest(properties = {
		"spring.flyway.enabled=true",
		"spring.flyway.locations=filesystem:${cloud.migrations.dir}"
})
abstract class CloudPostgresIntegrationTest {

	@ServiceConnection
	static final PostgreSQLContainer POSTGRES =
			new PostgreSQLContainer(DockerImageName.parse("postgres:16"));

	static {
		POSTGRES.start();
	}
}
