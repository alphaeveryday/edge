package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.dto.ApproveRequest;
import com.edge.tenantconsole.dto.ExplanationRejectRequest;
import com.edge.tenantconsole.dto.ExplanationResponse;
import com.edge.tenantconsole.dto.FeedStatusResponse;
import com.edge.tenantconsole.dto.FinalRequest;
import com.edge.tenantconsole.service.ExplanationService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 가격 변동 설명 표면(ALPHA-513) — tenant-console-ui explanations 도메인 계약
 * (repository.real.ts)과 1:1. 필드명은 UI 타입과 동일한 camelCase 를 쓴다
 * (기존 검수 표면의 snake_case 와 다른 이유 — UI 계약이 SSOT). 와이어 타입은 dto 패키지.
 */
@RestController
public class ExplanationController {

	private final ExplanationService explanationService;

	public ExplanationController(ExplanationService explanationService) {
		this.explanationService = explanationService;
	}

	@GetMapping("/api/v1/explanations")
	public ApiResponse<List<ExplanationResponse>> list() {
		return ApiResponse.onSuccess(
				explanationService.list().stream().map(ExplanationResponse::from).toList());
	}

	@GetMapping("/api/v1/explanations/feed-status")
	public ApiResponse<FeedStatusResponse> feedStatus() {
		return ApiResponse.onSuccess(FeedStatusResponse.from(explanationService.feedStatus()));
	}

	@PatchMapping("/api/v1/explanations/{id}/final")
	public ApiResponse<Void> updateFinal(@PathVariable("id") String id,
			@RequestBody(required = false) FinalRequest request) {
		explanationService.updateFinal(id, request == null ? null : request.finalText());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/explanations/{id}/stop")
	public ApiResponse<Void> stop(@PathVariable("id") String id) {
		explanationService.stop(id);
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/explanations/{id}/move-to-review")
	public ApiResponse<Void> moveToReview(@PathVariable("id") String id) {
		explanationService.moveToReview(id);
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/explanations/{id}/approve")
	public ApiResponse<Void> approve(@PathVariable("id") String id,
			@RequestBody(required = false) ApproveRequest request) {
		explanationService.approve(id, request == null ? null : request.finalText());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/explanations/{id}/reject")
	public ApiResponse<Void> reject(@PathVariable("id") String id,
			@RequestBody(required = false) ExplanationRejectRequest request) {
		explanationService.reject(id, request == null ? null : request.note());
		return ApiResponse.onSuccess(null);
	}

	@PatchMapping("/api/v1/explanations/{id}/draft")
	public ApiResponse<Void> saveDraft(@PathVariable("id") String id,
			@RequestBody(required = false) FinalRequest request) {
		explanationService.saveDraft(id, request == null ? null : request.finalText());
		return ApiResponse.onSuccess(null);
	}
}
