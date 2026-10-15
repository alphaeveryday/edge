package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.ExplanationResponse;
import com.edge.tenantconsole.dto.FeedStatusResponse;
import com.edge.tenantconsole.dto.FinalRequest;
import com.edge.tenantconsole.dto.StopRequest;
import com.edge.tenantconsole.service.ExplanationService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 가격 변동 설명 표면(ALPHA-513·607·613) — tenant-console-ui explanations 도메인 계약
 * (repository.real.ts)과 1:1. 필드명은 UI 타입과 동일한 camelCase 를 쓴다(기존 검수
 * 표면의 snake_case 와 다른 이유 — UI 계약이 SSOT). 조회는 현황판, 쓰기는 사후 운영
 * (최종 문구 정정·제공 중단·검수 이관)이다. HTTP 관심사만 — 감사 주체(actor)는 세션,
 * client IP 는 요청에서 뽑아 서비스에 넘긴다(ReviewController 와 동일 패턴). 인증·역할
 * 강제는 ConsoleAuthFilter 가 수행한다(permission-matrix.md). 와이어 타입은 dto 패키지.
 */
@RestController
public class ExplanationController {

	private final ExplanationService explanationService;

	public ExplanationController(ExplanationService explanationService) {
		this.explanationService = explanationService;
	}

	@GetMapping("/api/v1/explanations")
	public ApiResponse<List<ExplanationResponse>> list(
			@RequestParam(value = "limit", defaultValue = "50") int limit,
			@RequestParam(value = "offset", defaultValue = "0") int offset) {
		return ApiResponse.onSuccess(
				explanationService.list(ListPaging.clampLimit(limit), ListPaging.clampOffset(offset))
						.stream().map(ExplanationResponse::from).toList());
	}

	/** 단건 조회(ALPHA-914) — 목록 페이지네이션 도입으로 상세 딥링크가 목록 캐시에 의존할 수 없어 신설. */
	@GetMapping("/api/v1/explanations/{id}")
	public ApiResponse<ExplanationResponse> detail(@PathVariable("id") String id) {
		return ApiResponse.onSuccess(ExplanationResponse.from(explanationService.detail(id)));
	}

	/** 상태별 건수(ALPHA-914) — 대시보드 KPI·검수 대기 배지가 페이지 목록 대신 소비한다. */
	@GetMapping("/api/v1/explanations/status-counts")
	public ApiResponse<Map<String, Long>> statusCounts() {
		return ApiResponse.onSuccess(explanationService.statusCounts());
	}

	@GetMapping("/api/v1/explanations/feed-status")
	public ApiResponse<FeedStatusResponse> feedStatus() {
		return ApiResponse.onSuccess(FeedStatusResponse.from(explanationService.feedStatus()));
	}

	@PatchMapping("/api/v1/explanations/{id}/final")
	public ApiResponse<Void> updateFinal(@PathVariable("id") String id,
			@RequestBody(required = false) FinalRequest request, HttpServletRequest httpRequest) {
		explanationService.updateFinal(id, request == null ? null : request.finalText(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/explanations/{id}/stop")
	public ApiResponse<Void> stop(@PathVariable("id") String id,
			@RequestBody(required = false) StopRequest request, HttpServletRequest httpRequest) {
		explanationService.stop(id, request == null ? null : request.reason(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/explanations/{id}/move-to-review")
	public ApiResponse<Void> moveToReview(@PathVariable("id") String id,
			HttpServletRequest httpRequest) {
		explanationService.moveToReview(id, actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	// 필터가 이 표면들의 인증을 보장하므로 세션·주체는 항상 존재한다(ReviewController 와 동일).
	private static SessionMember actor(HttpServletRequest request) {
		return (SessionMember) request.getSession(false).getAttribute(SessionMember.SESSION_KEY);
	}
}
