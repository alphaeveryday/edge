package com.edge.tenantsync;

import org.junit.jupiter.api.Test;

/**
 * 실 Postgres(migrations-cloud 적용) 위에서 부팅을 확인한다 — 테스트 클래스패스에
 * Flyway 자동설정이 있어(통합 테스트 인프라, ALPHA-572) DB 없는 부팅은 성립하지 않고,
 * 실 스키마 위 부팅이 곧 datasource·마이그레이션 배선 검증이다(super-admin-api 선례).
 */
class TenantSyncApplicationTests extends CloudPostgresIntegrationTest {

	@Test
	void contextLoads() {
	}

}
