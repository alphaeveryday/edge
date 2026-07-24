package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.repository.ReviewItemRepository.ReviewItem;
import com.edge.tenantconsole.service.ReviewService;
import com.fasterxml.jackson.annotation.JsonInclude;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.util.List;
import java.util.Set;

/**
 * 검수 표면 — Review Queue 목록 + 검수 액션(승인·반려). HTTP 관심사만 담당.
 * 인증·역할 강제는 ConsoleAuthFilter 가 수행한다(검수 액션 = Compliance Reviewer
 * 전용 — docs/console-ia/permission-matrix.md).
 */
@RestController
public class ReviewController {

	// 상태 필터 어휘 = analysis_item.status CHECK(state-machine.md)와 동일.
	private static final Set<String> STATUSES = Set.of(
			"RECEIVED", "AUTO_PUBLISHED", "REVIEW_REQUIRED", "APPROVED",
			"REJECTED", "BLOCKED", "UNPUBLISHED", "CORRECTED", "INVALIDATED");

	private final ReviewService reviewService;

	public ReviewController(ReviewService reviewService) {
		this.reviewService = reviewService;
	}

	@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
	@JsonInclude(JsonInclude.Include.NON_NULL)
	public record ReviewItemResponse(
			String explanationResultId,
			String etfTicker,
			String etfName,
			String tradeDate,
			String summary,
			String headline,
			String confidenceLevel,
			String status,
			String supersedesItemId,
			String correctionReason,
			String receivedAt
	) {
		static ReviewItemResponse from(ReviewItem i) {
			return new ReviewItemResponse(
					i.explanationResultId(), i.etfTicker(), i.etfName(),
					i.tradeDate() == null ? null : i.tradeDate().toString(),
					i.summary(), i.headline(), i.confidenceLevel(), i.status(),
					i.supersedesItemId(), i.correctionReason(),
					i.receivedAt() == null ? null : i.receivedAt().toString());
		}
	}

	public record RejectRequest(String reason) {
	}

	@GetMapping("/api/v1/review/items")
	public ApiResponse<List<ReviewItemResponse>> list(
			@RequestParam(value = "status", defaultValue = "REVIEW_REQUIRED") String status) {
		if (!STATUSES.contains(status)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_STATUS_FILTER);
		}
		return ApiResponse.onSuccess(
				reviewService.list(status).stream().map(ReviewItemResponse::from).toList());
	}

	@PostMapping("/api/v1/review/items/{id}/approve")
	public ApiResponse<Void> approve(@PathVariable("id") String id) {
		reviewService.approve(id);
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/review/items/{id}/reject")
	public ApiResponse<Void> reject(@PathVariable("id") String id,
			@RequestBody(required = false) RejectRequest request) {
		reviewService.reject(id, request == null ? null : request.reason());
		return ApiResponse.onSuccess(null);
	}
}
