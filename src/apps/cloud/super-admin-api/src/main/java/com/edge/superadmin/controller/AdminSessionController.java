package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.dto.OperatorProfileResponse;
import com.edge.superadmin.dto.ProfileRequest;
import com.edge.superadmin.service.AdminSessionService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpSession;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 콘솔 세션 화면 표면(ALPHA-515·608) — super-admin-ui session 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. 사이드바·헤더가 쓰는 운영자 컨텍스트를
 * 인증 세션 주체(SessionOperator)로부터 반환한다 — AdminAuthFilter 가 인증을
 * 보장하므로 여기 도달하면 세션과 주체는 항상 존재한다(AuthController 와 동일 전제).
 */
@RestController
public class AdminSessionController {

	private final AdminSessionService adminSessionService;

	public AdminSessionController(AdminSessionService adminSessionService) {
		this.adminSessionService = adminSessionService;
	}

	@GetMapping("/api/v1/session")
	public ApiResponse<OperatorProfileResponse> current(HttpServletRequest httpRequest) {
		return ApiResponse.onSuccess(adminSessionService.profileOf(operator(httpRequest)));
	}

	@PatchMapping("/api/v1/session/profile")
	public ApiResponse<Void> updateProfile(@RequestBody(required = false) ProfileRequest request,
			HttpServletRequest httpRequest) {
		String name = adminSessionService.validatedDisplayName(request == null ? null : request.name());
		// 표시 이름 변경을 세션 주체에 재바인딩 — /session·/auth/session 이 같은 이름을
		// 본다(단일 원천). 계정 원장(config)은 불변이라 변경은 세션 범위로만 남는다.
		HttpSession session = httpRequest.getSession(false);
		SessionOperator operator = (SessionOperator) session.getAttribute(SessionOperator.SESSION_KEY);
		session.setAttribute(SessionOperator.SESSION_KEY, new SessionOperator(operator.email(), name));
		return ApiResponse.onSuccess(null);
	}

	private SessionOperator operator(HttpServletRequest httpRequest) {
		return (SessionOperator) httpRequest.getSession(false).getAttribute(SessionOperator.SESSION_KEY);
	}
}
