package com.edge.publication;

import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.nio.file.Files;
import java.nio.file.Path;

/**
 * 온프렘 스키마 정합을 실 Postgres 로 검증하는 통합 테스트의 공통 베이스.
 * 스키마 SSOT 는 Flyway(libs/schema)이므로 컨테이너에 migrations-onprem 을 적용한 뒤
 * Hibernate ddl-auto=validate 로 엔티티↔스키마를 대조한다(ADR-0038). H2 는 JSONB·IDENTITY 등
 * Postgres 문법을 지원하지 않아 이 검증을 대체할 수 없다.
 *
 * <p>컨테이너는 <b>싱글턴</b>이다 — 여러 테스트 클래스가 같은 @SpringBootTest 설정의 캐시된
 * 컨텍스트(=하나의 datasource)를 공유하므로, @Container 로 클래스마다 멈추면 다음 클래스의
 * datasource 가 죽은 컨테이너를 가리킨다. static 블록에서 한 번 띄우고 멈추지 않으며 JVM 종료 시
 * Testcontainers(Ryuk)가 회수한다.
 */
@SpringBootTest
abstract class OnpremPostgresIntegrationTest {

	@ServiceConnection
	static final PostgreSQLContainer POSTGRES =
			new PostgreSQLContainer(DockerImageName.parse("postgres:16"));

	static {
		POSTGRES.start();
	}

	/**
	 * onprem 마이그레이션을 컨테이너에 적용한다. 위치는 테스트 작업 디렉터리에 무관하게 상위로
	 * 올라가며 찾은 절대 경로로 준다 — Gradle 모듈 디렉터리·src·레포 루트 어디서 실행되든 성립한다.
	 */
	@DynamicPropertySource
	static void onpremFlyway(DynamicPropertyRegistry registry) {
		registry.add("spring.flyway.enabled", () -> true);
		registry.add("spring.flyway.locations", () -> "filesystem:" + locateOnpremMigrations());
	}

	private static String locateOnpremMigrations() {
		Path dir = Path.of("").toAbsolutePath();
		for (int depth = 0; depth < 8 && dir != null; depth++, dir = dir.getParent()) {
			for (String rel : new String[] {"src/libs/schema/migrations-onprem", "libs/schema/migrations-onprem"}) {
				Path candidate = dir.resolve(rel);
				if (Files.isDirectory(candidate)) {
					return candidate.toString();
				}
			}
		}
		throw new IllegalStateException(
				"migrations-onprem 디렉터리를 찾을 수 없다 (from " + Path.of("").toAbsolutePath() + ")");
	}
}
