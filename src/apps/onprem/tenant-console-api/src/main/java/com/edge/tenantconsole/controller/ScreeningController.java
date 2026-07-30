package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.AddWordRequest;
import com.edge.tenantconsole.dto.BannedWordResponse;
import com.edge.tenantconsole.dto.CriteriaPatchRequest;
import com.edge.tenantconsole.dto.CriteriaResponse;
import com.edge.tenantconsole.dto.DisclaimerRequest;
import com.edge.tenantconsole.dto.DisclaimerResponse;
import com.edge.tenantconsole.dto.PolicyVersionResponse;
import com.edge.tenantconsole.service.ScreeningService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 점검 정책 표면(ALPHA-438) — tenant-console-ui screening 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. 면책 문구는 {text} 로 감싼다 — 원시 문자열
 * 응답은 JSON 파싱 계약(apiClient)과 어긋난다. 변경(발행)은 감사 주체가 필요해
 * 세션 actor 를 서비스로 전달한다(ConsoleAuthFilter 가 non-null 을 보장).
 */
@RestController
public class ScreeningController {

	private final ScreeningService screeningService;

	public ScreeningController(ScreeningService screeningService) {
		this.screeningService = screeningService;
	}

	@GetMapping("/api/v1/screening/words")
	public ApiResponse<List<BannedWordResponse>> listWords() {
		return ApiResponse.onSuccess(
				screeningService.listWords().stream().map(BannedWordResponse::from).toList());
	}

	@PostMapping("/api/v1/screening/words")
	public ApiResponse<Void> addWord(@RequestBody(required = false) AddWordRequest request,
			HttpServletRequest httpRequest) {
		screeningService.addWord(
				request == null ? null : request.text(),
				request == null ? null : request.risk(),
				request == null ? null : request.action(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@PostMapping("/api/v1/screening/words/{id}/toggle")
	public ApiResponse<Void> toggleWord(@PathVariable("id") long id, HttpServletRequest httpRequest) {
		screeningService.toggleWord(id, actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@GetMapping("/api/v1/screening/criteria")
	public ApiResponse<CriteriaResponse> getCriteria() {
		return ApiResponse.onSuccess(CriteriaResponse.from(screeningService.getCriteria()));
	}

	@PatchMapping("/api/v1/screening/criteria")
	public ApiResponse<Void> updateCriteria(
			@RequestBody(required = false) CriteriaPatchRequest request,
			HttpServletRequest httpRequest) {
		screeningService.updateCriteria(
				request == null ? null : request.minSources(),
				request == null ? null : request.maxRisk(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@GetMapping("/api/v1/screening/disclaimer")
	public ApiResponse<DisclaimerResponse> getDisclaimer() {
		return ApiResponse.onSuccess(new DisclaimerResponse(screeningService.getDisclaimer()));
	}

	@PatchMapping("/api/v1/screening/disclaimer")
	public ApiResponse<Void> updateDisclaimer(
			@RequestBody(required = false) DisclaimerRequest request,
			HttpServletRequest httpRequest) {
		screeningService.updateDisclaimer(request == null ? null : request.text(),
				actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	@GetMapping("/api/v1/screening/versions")
	public ApiResponse<List<PolicyVersionResponse>> listVersions() {
		return ApiResponse.onSuccess(
				screeningService.listVersions().stream().map(PolicyVersionResponse::from).toList());
	}

	private static SessionMember actor(HttpServletRequest request) {
		return (SessionMember) request.getSession(false).getAttribute(SessionMember.SESSION_KEY);
	}
}
