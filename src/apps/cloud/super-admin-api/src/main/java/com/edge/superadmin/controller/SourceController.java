package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.dto.SourceReportResponse;
import com.edge.superadmin.service.SourceService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 데이터 소스 수집 상태 표면(ALPHA-515) — super-admin-ui sources 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase.
 */
@RestController
public class SourceController {

	private final SourceService sourceService;

	public SourceController(SourceService sourceService) {
		this.sourceService = sourceService;
	}

	@GetMapping("/api/v1/sources/report")
	public ApiResponse<SourceReportResponse> report() {
		return ApiResponse.onSuccess(SourceReportResponse.from(sourceService.report()));
	}
}
