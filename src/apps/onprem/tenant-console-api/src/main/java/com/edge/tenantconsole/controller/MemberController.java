package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.dto.InviteRequest;
import com.edge.tenantconsole.dto.MemberResponse;
import com.edge.tenantconsole.service.MemberService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 사용자·권한 표면(ALPHA-513) — tenant-console-ui users 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. member 원장(MemberRepository)과 별개의
 * mock 표면이다 — 정합은 DB 연동(ALPHA-119)에서 맞춘다. 와이어 타입은 dto 패키지.
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

	@PostMapping("/api/v1/members/invitations")
	public ApiResponse<Void> invite(@RequestBody(required = false) InviteRequest request) {
		memberService.invite(
				request == null ? null : request.email(),
				request == null ? null : request.role());
		return ApiResponse.onSuccess(null);
	}
}
