package com.edge.superadmin;

import org.junit.jupiter.api.Test;

/**
 * 컨텍스트 기동 = 엔티티↔스키마 정합 검증. Testcontainers Postgres 에 migrations-cloud 를
 * 적용한 뒤 Hibernate ddl-auto=validate 가 Tenant 엔티티 매핑을 실 tenant 스키마와 대조한다 —
 * 컬럼명·타입이 어긋나면 여기서 기동이 실패한다(Rule 9). JdbcTemplate 모듈과 달리 JPA 는
 * 부팅 시 즉시 연결하므로 실 DB 가 필요하다(ALPHA-526).
 */
class SuperAdminApplicationTests extends CloudPostgresIntegrationTest {

	@Test
	void contextLoads() {
	}

}
