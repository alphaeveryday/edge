package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.ServingRequestMetricEntity;
import com.edge.tenantconsole.model.TrafficSummary;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;

import java.time.Instant;

/**
 * Dashboard 트래픽 집계(ALPHA-128) — serving_request_metric 읽기 전용. 요청당 1행
 * 원장을 조회 시점에 집계한다(사전 집계 없음 — DDL 주석의 설계).
 */
public interface DashboardMetricRepository extends Repository<ServingRequestMetricEntity, Long> {

	/** 빈 윈도의 SUM 은 NULL 이라 COALESCE 로 0 을 강제한다 — 트래픽 없음은 정상 상태다. */
	@Query("""
			SELECT new com.edge.tenantconsole.model.TrafficSummary(
			    COUNT(m),
			    COALESCE(SUM(CASE WHEN m.statusCode >= 400 THEN 1 ELSE 0 END), 0))
			FROM ServingRequestMetricEntity m
			WHERE m.occurredAt >= :since
			""")
	TrafficSummary summarizeSince(@Param("since") Instant since);
}
