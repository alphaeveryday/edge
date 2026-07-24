package com.edge.screening;

import com.edge.screening.scheduler.ScreeningPoller;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

/**
 * 온프렘 스키마 정합을 실 Postgres 로 검증하는 통합 테스트의 공통 베이스.
 * 스키마 SSOT 는 Flyway(libs/schema)이므로 컨테이너에 migrations-onprem 을 적용한 뒤
 * Hibernate ddl-auto=validate 로 엔티티↔스키마를 대조한다(ADR-0038). H2 는 JSONB·IDENTITY 등
 * Postgres 문법을 지원하지 않아 이 검증을 대체할 수 없다.
 *
 * <p>flyway.locations 는 이 모듈 디렉터리(src/apps/onprem/screening-worker) 기준 상대 경로다 —
 * Gradle test 태스크의 작업 디렉터리가 모듈 디렉터리라 성립한다.
 */
@SpringBootTest(properties = {
		"spring.flyway.enabled=true",
		"spring.flyway.locations=filesystem:../../../libs/schema/migrations-onprem"
})
@Testcontainers
abstract class OnpremPostgresIntegrationTest {

	@Container
	@ServiceConnection
	static final PostgreSQLContainer POSTGRES =
			new PostgreSQLContainer(DockerImageName.parse("postgres:16"));

	// @Scheduled 폴러(ScreeningPoller)를 무력화한다 — 통합테스트 컨텍스트는 캐시돼 살아 있어 폴러가
	// 배경에서 미점검분을 점검·마킹하면 같은 Testcontainers DB 의 analysis_item·publication·
	// received_bundle 이 흔들려 테스트가 비결정적이 된다. Mockito 대체 빈은 메서드 애너테이션
	// (@Scheduled)을 물려받지 않아 스케줄되지 않는다.
	@MockitoBean
	ScreeningPoller screeningPoller;
}
