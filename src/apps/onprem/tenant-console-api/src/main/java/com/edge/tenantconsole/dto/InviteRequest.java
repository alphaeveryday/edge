package com.edge.tenantconsole.dto;

/**
 * 멤버 초대 요청 — 이메일·역할. MemberController POST /api/v1/members/invitations.
 */
public record InviteRequest(String email, String role) {
}
