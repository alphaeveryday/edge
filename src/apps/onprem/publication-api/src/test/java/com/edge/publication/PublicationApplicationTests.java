package com.edge.publication;

import org.junit.jupiter.api.Test;

/**
 * 컨텍스트 기동 = 엔티티↔스키마 정합 검증. Testcontainers Postgres 에 onprem 마이그레이션을
 * 적용한 뒤 Hibernate ddl-auto=validate 가 3개 엔티티(publication·analysis_item·exposure_log)의
 * 매핑을 실제 스키마와 대조한다 — 컬럼명·타입이 어긋나면 여기서 기동이 실패한다(Rule 9).
 */
class PublicationApplicationTests extends OnpremPostgresIntegrationTest {

	@Test
	void contextLoads() {
	}

}
