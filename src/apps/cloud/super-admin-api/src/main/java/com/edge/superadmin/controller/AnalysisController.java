package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.dto.AnalysisResponse;
import com.edge.superadmin.dto.CorrectRequest;
import com.edge.superadmin.dto.ReasonRequest;
import com.edge.superadmin.service.AnalysisService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.SessionAttribute;

import java.util.List;

/**
 * 가격 변동 분석 표면 — super-admin-ui analyses 도메인 계약과 1:1. 필드명은 UI 타입과
 * 동일한 camelCase. 읽기는 설명 원장 실조회(ALPHA-601), 쓰기는 원장 전이 + 감사(ALPHA-602)다.
 * 작업자는 세션 운영자(AdminAuthFilter 가 이 표면들에 인증을 강제 — 세션 보장)에서 얻는다.
 */
@RestController
public class AnalysisController {

	private final AnalysisService analysisService;

	public AnalysisController(AnalysisService analysisService) {
		this.analysisService = analysisService;
	}

	@GetMapping("/api/v1/analyses")
	public ApiResponse<List<AnalysisResponse>> list() {
		return ApiResponse.onSuccess(analysisService.list());
	}

	@PatchMapping("/api/v1/analyses/{id}/result")
	public ApiResponse<Void> correct(@PathVariable String id,
			@RequestBody(required = false) CorrectRequest request,
			@SessionAttribute(SessionOperator.SESSION_KEY) SessionOperator operator) {
		analysisService.correct(id, request == null ? null : request.result(),
				request == null ? null : request.reason(), operator);
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/analyses/{id}/exclude")
	public ApiResponse<Void> exclude(@PathVariable String id,
			@RequestBody(required = false) ReasonRequest request,
			@SessionAttribute(SessionOperator.SESSION_KEY) SessionOperator operator) {
		analysisService.exclude(id, request == null ? null : request.reason(), operator);
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/analyses/{id}/restore")
	public ApiResponse<Void> restore(@PathVariable String id,
			@RequestBody(required = false) ReasonRequest request,
			@SessionAttribute(SessionOperator.SESSION_KEY) SessionOperator operator) {
		analysisService.restore(id, request == null ? null : request.reason(), operator);
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/analyses/{id}/invalidate")
	public ApiResponse<Void> invalidate(@PathVariable String id,
			@RequestBody(required = false) ReasonRequest request,
			@SessionAttribute(SessionOperator.SESSION_KEY) SessionOperator operator) {
		analysisService.invalidate(id, request == null ? null : request.reason(), operator);
		return ApiResponse.onSuccess(null);
	}
}
