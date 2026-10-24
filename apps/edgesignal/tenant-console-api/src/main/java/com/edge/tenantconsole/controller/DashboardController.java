package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.dto.TrafficResponse;
import com.edge.tenantconsole.service.DashboardService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Dashboard 표면(ALPHA-128) — tenant-console-ui dashboard 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase(ALPHA-513 계열 규약).
 */
@RestController
public class DashboardController {

	private final DashboardService dashboardService;

	public DashboardController(DashboardService dashboardService) {
		this.dashboardService = dashboardService;
	}

	@GetMapping("/api/v1/dashboard/traffic")
	public ApiResponse<TrafficResponse> traffic() {
		return ApiResponse.onSuccess(TrafficResponse.from(dashboardService.traffic()));
	}
}
