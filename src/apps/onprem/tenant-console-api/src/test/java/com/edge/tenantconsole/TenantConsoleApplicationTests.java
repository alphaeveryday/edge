package com.edge.tenantconsole;

import org.junit.jupiter.api.Test;

/**
 * 엔티티↔실스키마 정합 검증 — 베이스(Testcontainers Postgres + Flyway migrations-onprem)가
 * 앱을 기동하고 ddl-auto=validate 가 3개 엔티티(member·analysis_item·publication) 매핑을
 * 실테이블과 대조한다. 매핑이 스키마와 어긋나면 기동 실패로 잡는다(Rule 12).
 */
class TenantConsoleApplicationTests extends AbstractPostgresIntegrationTest {

	@Test
	void contextLoads() {
	}
}
