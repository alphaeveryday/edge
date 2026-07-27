package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.dto.SourceReportResponse;
import com.edge.superadmin.service.SourceService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 데이터 소스 수집 상태 표면 — super-admin-ui sources 도메인 계약과 1:1(ALPHA-515 → 514).
 * 필드명은 UI 타입과 동일한 camelCase. 응답 조립은 서비스가 하고 여기선 감싸기만 한다.
 */
@RestController
public class SourceController {

	private final SourceService sourceService;

	public SourceController(SourceService sourceService) {
		this.sourceService = sourceService;
	}

	/**
	 * @param runKey 볼 런의 슬롯 키. <b>선택</b>이라 없으면 지금까지처럼 최신 런이다 — 드릴다운은
	 *               새 엔드포인트가 아니라 이 화면을 <b>주소 지정 가능</b>하게 만든 것뿐이다.
	 */
	@GetMapping("/api/v1/sources/report")
	public ApiResponse<SourceReportResponse> report(
			@RequestParam(required = false) String runKey) {
		return ApiResponse.onSuccess(sourceService.report(runKey));
	}
}
