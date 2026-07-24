package com.edge.tenantconsole;

import org.junit.jupiter.api.condition.EnabledIf;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.DockerClientFactory;
import org.testcontainers.postgresql.PostgreSQLContainer;

/**
 * Testcontainers Postgres 통합 테스트 베이스 — 스키마 SSOT(migrations-onprem)를 Flyway 로
 * 적용한 실 DB 에 앱을 기동한다(ddl-auto=validate 로 엔티티↔실테이블 정합 검증). Docker 가
 * 없으면 통째로 disable 한다 — JUnit 이 이를 "disabled"로 보고하므로 숨겨진 통과가 아니다.
 *
 * 컨테이너는 static 블록에서 1회 기동하는 싱글턴이다(@Container 생명주기 미사용) — 여러
 * 통합 테스트 클래스가 같은 컨테이너·Spring 컨텍스트를 공유하고, JVM 종료 시 Ryuk 이
 * 정리한다. (@Container 로 관리하면 클래스마다 start/stop 되어 두 번째 클래스가 종료된
 * 컨테이너에 붙어 연결 실패가 난다.)
 */
@SpringBootTest
@EnabledIf("dockerAvailable")
public abstract class AbstractPostgresIntegrationTest {

	static final PostgreSQLContainer postgres = new PostgreSQLContainer("postgres:16");

	static {
		if (DockerClientFactory.instance().isDockerAvailable()) {
			postgres.start();
		}
	}

	@DynamicPropertySource
	static void datasource(DynamicPropertyRegistry registry) {
		registry.add("spring.datasource.url", postgres::getJdbcUrl);
		registry.add("spring.datasource.username", postgres::getUsername);
		registry.add("spring.datasource.password", postgres::getPassword);
		// 앱 기본은 flyway.enabled=false — 통합 테스트에서만 실 마이그레이션으로 스키마를 세운다
		// (build.gradle 이 migrations-onprem 을 테스트 클래스패스 db/migration 으로 복사).
		registry.add("spring.flyway.enabled", () -> true);
		registry.add("spring.flyway.locations", () -> "classpath:db/migration");
	}

	public static boolean dockerAvailable() {
		return DockerClientFactory.instance().isDockerAvailable();
	}
}
