package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.config.TenantContextProperties;
import com.edge.tenantconsole.dto.ProfileRequest;
import com.edge.tenantconsole.dto.SessionUserResponse;
import com.edge.tenantconsole.service.ConsoleSessionService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 콘솔 세션 화면 표면 — tenant-console-ui session 도메인 계약과 1:1(camelCase).
 * 식별·표시 이름(name·email·role)은 실 인증 주체(SessionMember = member 원장)의
 * 값이고(ALPHA-119·500), 테넌트 컨텍스트는 배포 설정(console.tenant.*)이 소스다.
 * 와이어 타입은 dto 패키지.
 */
@RestController
public class ConsoleSessionController {

	private final ConsoleSessionService consoleSessionService;
	private final TenantContextProperties tenantContext;

	public ConsoleSessionController(ConsoleSessionService consoleSessionService,
			TenantContextProperties tenantContext) {
		this.consoleSessionService = consoleSessionService;
		this.tenantContext = tenantContext;
	}

	// 필터가 인증을 보장하므로 세션·주체는 항상 존재한다(AuthController 와 동일 패턴).
	@GetMapping("/api/v1/session")
	public ApiResponse<SessionUserResponse> current(HttpServletRequest httpRequest) {
		SessionMember member = actor(httpRequest);
		return ApiResponse.onSuccess(new SessionUserResponse(member.name(), member.email(),
				member.role(), tenantContext.name(), tenantContext.domain(), tenantContext.mark()));
	}

	@PatchMapping("/api/v1/session/profile")
	public ApiResponse<Void> updateProfile(@RequestBody(required = false) ProfileRequest request,
			HttpServletRequest httpRequest) {
		SessionMember member = actor(httpRequest);
		String updated = consoleSessionService.updateDisplayName(member.memberId(),
				request == null ? null : request.name());
		// 본인 세션은 다음 요청의 필터 재검증을 기다리지 않고 즉시 새 이름을 반영한다
		// (다른 세션은 ConsoleAuthFilter 의 원장 재검증이 반영).
		httpRequest.getSession(false).setAttribute(SessionMember.SESSION_KEY,
				new SessionMember(member.memberId(), member.email(), updated, member.role()));
		return ApiResponse.onSuccess(null);
	}

	private static SessionMember actor(HttpServletRequest request) {
		return (SessionMember) request.getSession(false).getAttribute(SessionMember.SESSION_KEY);
	}
}
