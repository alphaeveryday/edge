package com.edge.tenantconsole.dto;

/** 역할 변경 요청(ALPHA-499) — role 은 원장 4종 어휘(ck_member_role)여야 한다. */
public record ChangeMemberRoleRequest(String role) {
}
