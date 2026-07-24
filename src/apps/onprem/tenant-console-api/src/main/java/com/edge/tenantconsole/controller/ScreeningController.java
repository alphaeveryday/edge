package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.mock.ScreeningMockStore.AutoPublishCriteria;
import com.edge.tenantconsole.mock.ScreeningMockStore.BannedWord;
import com.edge.tenantconsole.service.ScreeningService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 점검 기준 표면(ALPHA-513) — tenant-console-ui screening 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. 면책 문구는 {text} 로 감싼다 — 원시 문자열
 * 응답은 JSON 파싱 계약(apiClient)과 어긋난다.
 */
@RestController
public class ScreeningController {

	private final ScreeningService screeningService;

	public ScreeningController(ScreeningService screeningService) {
		this.screeningService = screeningService;
	}

	public record BannedWordResponse(long id, String text, String risk, String action,
			boolean active, String registeredAt) {
		static BannedWordResponse from(BannedWord w) {
			return new BannedWordResponse(w.id(), w.text(), w.risk(), w.action(), w.active(),
					w.registeredAt());
		}
	}

	public record CriteriaResponse(int minSources, String maxRisk) {
		static CriteriaResponse from(AutoPublishCriteria c) {
			return new CriteriaResponse(c.minSources(), c.maxRisk());
		}
	}

	public record AddWordRequest(String text, String risk, String action) {
	}

	/** PATCH 시맨틱 — null 필드는 유지. */
	public record CriteriaPatchRequest(Integer minSources, String maxRisk) {
	}

	public record DisclaimerResponse(String text) {
	}

	public record DisclaimerRequest(String text) {
	}

	@GetMapping("/api/v1/screening/words")
	public ApiResponse<List<BannedWordResponse>> listWords() {
		return ApiResponse.onSuccess(
				screeningService.listWords().stream().map(BannedWordResponse::from).toList());
	}

	@PostMapping("/api/v1/screening/words")
	public ApiResponse<Void> addWord(@RequestBody(required = false) AddWordRequest request) {
		screeningService.addWord(
				request == null ? null : request.text(),
				request == null ? null : request.risk(),
				request == null ? null : request.action());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/screening/words/{id}/toggle")
	public ApiResponse<Void> toggleWord(@PathVariable("id") long id) {
		screeningService.toggleWord(id);
		return ApiResponse.onSuccess(null);
	}

	@GetMapping("/api/v1/screening/criteria")
	public ApiResponse<CriteriaResponse> getCriteria() {
		return ApiResponse.onSuccess(CriteriaResponse.from(screeningService.getCriteria()));
	}

	@PatchMapping("/api/v1/screening/criteria")
	public ApiResponse<Void> updateCriteria(
			@RequestBody(required = false) CriteriaPatchRequest request) {
		screeningService.updateCriteria(
				request == null ? null : request.minSources(),
				request == null ? null : request.maxRisk());
		return ApiResponse.onSuccess(null);
	}

	@GetMapping("/api/v1/screening/disclaimer")
	public ApiResponse<DisclaimerResponse> getDisclaimer() {
		return ApiResponse.onSuccess(new DisclaimerResponse(screeningService.getDisclaimer()));
	}

	@PatchMapping("/api/v1/screening/disclaimer")
	public ApiResponse<Void> updateDisclaimer(
			@RequestBody(required = false) DisclaimerRequest request) {
		screeningService.updateDisclaimer(request == null ? null : request.text());
		return ApiResponse.onSuccess(null);
	}
}
