package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.TrafficSummary;

/** Dashboard 트래픽 KPI 와이어 계약 — tenant-console-ui dashboard 도메인과 1:1(camelCase). */
public record TrafficResponse(long totalRequests, long errorRequests) {

	public static TrafficResponse from(TrafficSummary summary) {
		return new TrafficResponse(summary.totalRequests(), summary.errorRequests());
	}
}
