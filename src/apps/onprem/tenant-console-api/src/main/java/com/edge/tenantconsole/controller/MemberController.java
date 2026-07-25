package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.CreateMemberRequest;
import com.edge.tenantconsole.dto.MemberResponse;
import com.edge.tenantconsole.service.MemberService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 콘솔 사용자 관리 표면(ALPHA-119) — 목록·등록·비활성화. member 원장 실데이터.
 * HTTP 관심사만: 감사 주체(actor)는 세션에서, client IP 는 요청에서 뽑아 서비스에
 * 넘긴다. 인가(TENANT_ADMIN)는 ConsoleAuthFilter 소관이라 여기서 role 을 다시 검사하지
 * 않는다. 필드는 tenant-console-ui users 타입과 1:1 camelCase.
 */
@RestController
public class MemberController {

	private final MemberService memberService;

	public MemberController(MemberService memberService) {
		this.memberService = memberService;
	}

	@GetMapping("/api/v1/members")
	public ApiResponse<List<MemberResponse>> list() {
		return ApiResponse.onSuccess(
				memberService.list().stream().map(MemberResponse::from).toList());
	}

	@PostMapping("/api/v1/members")
	public ApiResponse<MemberResponse> register(
			@RequestBody(required = false) CreateMemberRequest request,
			HttpServletRequest httpRequest) {
		return ApiResponse.onSuccess(MemberResponse.from(
				memberService.register(request, actor(httpRequest), httpRequest.getRemoteAddr())));
	}

	@PostMapping("/api/v1/members/{memberId}/deactivate")
	public ApiResponse<Void> deactivate(@PathVariable long memberId,
			HttpServletRequest httpRequest) {
		memberService.deactivate(memberId, actor(httpRequest), httpRequest.getRemoteAddr());
		return ApiResponse.onSuccess(null);
	}

	// 필터가 이 표면들의 인증을 보장하므로 세션·주체는 항상 존재한다(AuthController 와 동일 패턴).
	private static SessionMember actor(HttpServletRequest request) {
		return (SessionMember) request.getSession(false).getAttribute(SessionMember.SESSION_KEY);
	}
}
