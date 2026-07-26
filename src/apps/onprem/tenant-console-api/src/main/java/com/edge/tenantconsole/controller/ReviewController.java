package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.ReviewApproveRequest;
import com.edge.tenantconsole.dto.ReviewBlockRequest;
import com.edge.tenantconsole.dto.ReviewEditedApproveRequest;
import com.edge.tenantconsole.dto.ReviewItemResponse;
import com.edge.tenantconsole.dto.ReviewRejectRequest;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.service.ReviewService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Set;

/**
 * 검수 표면 — Review Queue 목록 + 검수 액션(승인·수정 승인·반려·차단, ALPHA-437).
 * HTTP 관심사만: 감사 주체(actor)는 세션에서, client IP 는 요청에서 뽑아 서비스에
 * 넘긴다(MemberController 와 동일 패턴). 인증·역할 강제는 ConsoleAuthFilter 가
 * 수행한다(검수 액션 = Compliance Reviewer 전용 — docs/console-ia/permission-matrix.md).
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
	public ApiResponse<Void> approve(@PathVariable("id") String id,
			@RequestBody(required = false) ReviewApproveRequest request,
			HttpServletRequest httpRequest) {
		reviewService.approve(id, request == null ? null : request.note(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/review/items/{id}/approve-edited")
	public ApiResponse<Void> approveEdited(@PathVariable("id") String id,
			@RequestBody(required = false) ReviewEditedApproveRequest request,
			HttpServletRequest httpRequest) {
		reviewService.approveEdited(id,
				request == null ? null : request.editedSummary(),
				request == null ? null : request.note(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/review/items/{id}/reject")
	public ApiResponse<Void> reject(@PathVariable("id") String id,
			@RequestBody(required = false) ReviewRejectRequest request,
			HttpServletRequest httpRequest) {
		reviewService.reject(id, request == null ? null : request.reason(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/review/items/{id}/block")
	public ApiResponse<Void> block(@PathVariable("id") String id,
			@RequestBody(required = false) ReviewBlockRequest request,
			HttpServletRequest httpRequest) {
		reviewService.block(id, request == null ? null : request.reason(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	// 필터가 이 표면들의 인증을 보장하므로 세션·주체는 항상 존재한다(MemberController 와 동일).
	private static SessionMember actor(HttpServletRequest request) {
		return (SessionMember) request.getSession(false).getAttribute(SessionMember.SESSION_KEY);
	}
}
