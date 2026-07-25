package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.ProfileRequest;
import com.edge.tenantconsole.dto.SessionUserResponse;
import com.edge.tenantconsole.mock.SessionMockStore.SessionUser;
import com.edge.tenantconsole.service.ConsoleSessionService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 콘솔 세션 화면 표면(ALPHA-513) — tenant-console-ui session 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. **식별(role·email)은 실 인증 주체(SessionMember)의
 * 값으로 채운다** — UI 가 role 로 관리자 화면을 가르고 email 을 계정 팝오버에 표시하므로
 * mock 싱글턴이 아니라 로그인 사용자 본인이어야 한다(ALPHA-119). 표시 이름·테넌트 컨텍스트는
 * 현재 mock 이며 실데이터 전환은 ALPHA-500. 와이어 타입은 dto 패키지.
 */
@RestController
public class ConsoleSessionController {

	private final ConsoleSessionService consoleSessionService;

	public ConsoleSessionController(ConsoleSessionService consoleSessionService) {
		this.consoleSessionService = consoleSessionService;
	}

	// 필터가 인증을 보장하므로 세션·주체는 항상 존재한다(AuthController 와 동일 패턴).
	@GetMapping("/api/v1/session")
	public ApiResponse<SessionUserResponse> current(HttpServletRequest httpRequest) {
		SessionMember member = (SessionMember) httpRequest.getSession(false)
				.getAttribute(SessionMember.SESSION_KEY);
		SessionUser base = consoleSessionService.current();
		return ApiResponse.onSuccess(new SessionUserResponse(base.name(), member.email(),
				member.role(), base.tenantName(), base.tenantDomain(), base.tenantMark()));
	}

	@PatchMapping("/api/v1/session/profile")
	public ApiResponse<Void> updateProfile(@RequestBody(required = false) ProfileRequest request) {
		consoleSessionService.updateDisplayName(request == null ? null : request.name());
		return ApiResponse.onSuccess(null);
	}
}
