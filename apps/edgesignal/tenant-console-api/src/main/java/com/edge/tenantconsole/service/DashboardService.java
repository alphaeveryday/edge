package com.edge.tenantconsole.service;

import com.edge.tenantconsole.model.TrafficSummary;
import com.edge.tenantconsole.repository.DashboardMetricRepository;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.Instant;

@Service
public class DashboardService {

	/** Dashboard KPI 의 집계 윈도 — UI 라벨("24시간")과 한 몸인 계약. */
	private static final Duration TRAFFIC_WINDOW = Duration.ofHours(24);

	private final DashboardMetricRepository metrics;

	public DashboardService(DashboardMetricRepository metrics) {
		this.metrics = metrics;
	}

	public TrafficSummary traffic() {
		return metrics.summarizeSince(Instant.now().minus(TRAFFIC_WINDOW));
	}
}
