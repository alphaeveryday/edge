package com.edge.superadmin.controller;

import com.edge.superadmin.mock.AdminSessionMockStore.OperatorProfile;
import com.edge.superadmin.service.AdminSessionService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 콘솔 세션 화면 표면(ALPHA-515) — super-admin-ui session 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. 인증 세션 조회(AuthController /auth/session)와
 * 별개로, 사이드바·헤더가 쓰는 운영자 컨텍스트를 반환한다(현재 mock).
 */
@RestController
public class AdminSessionController {

	private final AdminSessionService adminSessionService;

	public AdminSessionController(AdminSessionService adminSessionService) {
		this.adminSessionService = adminSessionService;
	}

	public record ProfileRequest(String name) {
	}

	@GetMapping("/api/v1/session")
	public OperatorProfile current() {
		return adminSessionService.current();
	}

	@PatchMapping("/api/v1/session/profile")
	public ResponseEntity<Void> updateProfile(@RequestBody(required = false) ProfileRequest request) {
		adminSessionService.updateDisplayName(request == null ? null : request.name());
		return ResponseEntity.noContent().build();
	}
}
