package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.model.TrafficSummary;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.Duration;
import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Dashboard 트래픽 집계(ALPHA-128)의 DB 계약을 실 Postgres 로 검증한다 — 손수 대역이
 * 우회하는 실제 SQL 시맨틱이 WHY(Rule 9): 윈도 경계(occurred_at >= since)가 오래된
 * 행을 실제로 걸러내고, 에러 판정(status_code >= 400)이 4xx·5xx 만 세며, 빈 윈도의
 * SUM(NULL) 이 0 으로 강제(COALESCE)돼 첫 기동 대시보드가 깨지지 않는다.
 * 시드는 테스트 한정 JdbcTemplate — 실 writer 는 publication-api 요청 필터다.
 */
class DashboardMetricRepositoryIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private DashboardMetricRepository metrics;
	@Autowired
	private JdbcTemplate jdbc;

	private void seed(int statusCode, String errorCode, String age) {
		jdbc.update("""
				INSERT INTO serving_request_metric (method, route, status_code, error_code, occurred_at)
				VALUES ('GET', '/api/v1/explanations/{etfTicker}', ?, ?, now() - ?::interval)
				""", statusCode, errorCode, age);
	}

	@Test
	void 집계는_윈도_내_행만_세고_4xx_5xx_를_에러로_가른다() {
		// 공유 컨테이너 격리 — 이 테이블의 writer IT 는 이 클래스뿐이라 비우고 시작한다.
		jdbc.update("DELETE FROM serving_request_metric");
		seed(200, null, "1 hour");
		seed(204, null, "2 hours");
		seed(404, "SERV4040", "3 hours");
		seed(500, null, "4 hours");          // 비JSON 에러 등 코드 미상 실패도 에러로 집계
		seed(500, "SERV5000", "25 hours");   // 윈도 밖 — 총량·에러 어느 쪽에도 안 잡혀야

		TrafficSummary summary = metrics.summarizeSince(Instant.now().minus(Duration.ofHours(24)));

		assertThat(summary).isEqualTo(new TrafficSummary(4, 2));
	}

	@Test
	void 빈_윈도는_0_0_이다() {
		// WHY: 집계 대상 0건이면 SUM 이 NULL 이다 — COALESCE 없이는 NPE/null 매핑으로
		// 트래픽 없음(정상 상태)이 서버 에러가 된다.
		TrafficSummary summary = metrics.summarizeSince(Instant.now().plus(Duration.ofDays(1)));

		assertThat(summary).isEqualTo(new TrafficSummary(0, 0));
	}
}
