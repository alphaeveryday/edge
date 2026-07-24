package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.mock.SessionMockStore.SessionUser;
import com.edge.tenantconsole.service.ConsoleSessionService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 콘솔 세션 화면 표면(ALPHA-513) — tenant-console-ui session 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. 인증 세션 조회(AuthController /auth/session)와
 * 별개로, 사이드바·헤더가 쓰는 테넌트 컨텍스트를 반환한다(현재 mock).
 */
@RestController
public class ConsoleSessionController {

	private final ConsoleSessionService consoleSessionService;

	public ConsoleSessionController(ConsoleSessionService consoleSessionService) {
		this.consoleSessionService = consoleSessionService;
	}

	public record SessionUserResponse(String name, String email, String role, String tenantName,
			String tenantDomain, String tenantMark) {
		static SessionUserResponse from(SessionUser u) {
			return new SessionUserResponse(u.name(), u.email(), u.role(), u.tenantName(),
					u.tenantDomain(), u.tenantMark());
		}
	}

	public record ProfileRequest(String name) {
	}

	@GetMapping("/api/v1/session")
	public ApiResponse<SessionUserResponse> current() {
		return ApiResponse.onSuccess(SessionUserResponse.from(consoleSessionService.current()));
	}

	@PatchMapping("/api/v1/session/profile")
	public ApiResponse<Void> updateProfile(@RequestBody(required = false) ProfileRequest request) {
		consoleSessionService.updateDisplayName(request == null ? null : request.name());
		return ApiResponse.onSuccess(null);
	}
}
