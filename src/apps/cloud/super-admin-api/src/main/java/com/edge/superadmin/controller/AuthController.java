package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.service.AuthService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 인증 표면 — 로그인·로그아웃·세션 조회(HTTP 관심사만). 인가·차단은
 * AdminAuthFilter 소관이고, 이 컨트롤러의 login 만 공개 표면이다.
 */
@RestController
public class AuthController {

	private final AuthService authService;

	public AuthController(AuthService authService) {
		this.authService = authService;
	}

	public record LoginRequest(String email, String password) {
	}

	public record SessionResponse(String email, String name) {
		static SessionResponse from(SessionOperator operator) {
			return new SessionResponse(operator.email(), operator.name());
		}
	}

	@PostMapping("/api/v1/auth/login")
	public ApiResponse<SessionResponse> login(@RequestBody(required = false) LoginRequest request,
			HttpServletRequest httpRequest) {
		SessionOperator operator = authService.login(
				request == null ? null : request.email(),
				request == null ? null : request.password());
		// 로그인 성공 시 세션 ID 재발급 — 세션 고정(fixation) 차단.
		HttpSession existing = httpRequest.getSession(false);
		if (existing != null) {
			httpRequest.changeSessionId();
		}
		httpRequest.getSession(true).setAttribute(SessionOperator.SESSION_KEY, operator);
		return ApiResponse.onSuccess(SessionResponse.from(operator));
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
		SessionOperator operator = (SessionOperator) httpRequest.getSession(false)
				.getAttribute(SessionOperator.SESSION_KEY);
		return ApiResponse.onSuccess(SessionResponse.from(operator));
	}
}
