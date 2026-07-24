package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.service.AuthService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * 인증 표면 — 로그인·로그아웃·세션 조회(HTTP 관심사만). 인가·차단은
 * ConsoleAuthFilter 소관이고, 이 컨트롤러의 login 만 공개 표면이다.
 */
@RestController
public class AuthController {

	private final AuthService authService;

	public AuthController(AuthService authService) {
		this.authService = authService;
	}

	public record LoginRequest(String email, String password) {
	}

	@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
	public record SessionResponse(long memberId, String email, String name, String role) {
		static SessionResponse from(SessionMember member) {
			return new SessionResponse(member.memberId(), member.email(), member.name(), member.role());
		}
	}

	@PostMapping("/api/v1/auth/login")
	public ApiResponse<SessionResponse> login(@RequestBody(required = false) LoginRequest request,
			HttpServletRequest httpRequest) {
		SessionMember member = authService.login(
				request == null ? null : request.email(),
				request == null ? null : request.password());
		// 로그인 성공 시 세션 ID 재발급 — 세션 고정(fixation) 차단.
		HttpSession existing = httpRequest.getSession(false);
		if (existing != null) {
			httpRequest.changeSessionId();
		}
		httpRequest.getSession(true).setAttribute(SessionMember.SESSION_KEY, member);
		return ApiResponse.onSuccess(SessionResponse.from(member));
	}

	@PostMapping("/api/v1/auth/logout")
	public ApiResponse<Void> logout(HttpServletRequest httpRequest) {
		HttpSession session = httpRequest.getSession(false);
		if (session != null) {
			session.invalidate();
		}
		return ApiResponse.onSuccess(null);
	}

	// 필터가 인증을 보장하므로 여기 도달하면 세션은 항상 존재한다.
	@GetMapping("/api/v1/auth/session")
	public ApiResponse<SessionResponse> session(HttpServletRequest httpRequest) {
		SessionMember member = (SessionMember) httpRequest.getSession(false)
				.getAttribute(SessionMember.SESSION_KEY);
		return ApiResponse.onSuccess(SessionResponse.from(member));
	}
}
