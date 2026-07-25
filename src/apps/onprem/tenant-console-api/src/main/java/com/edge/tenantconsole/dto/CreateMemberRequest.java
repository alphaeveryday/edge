package com.edge.tenantconsole.dto;

/**
 * 사용자 등록 요청 — 관리자 직접 등록(초대 흐름 없음, ADR-0025). MemberController
 * POST /api/v1/members. role 은 원장 4종 어휘(TENANT_ADMIN·COMPLIANCE_REVIEWER·
 * OPERATOR·READ_ONLY). password 는 선택 — 있으면 데모 자체 계정(BCrypt), 없으면
 * SSO 전용(password_hash NULL, 데모 로컬 로그인 불가).
 */
public record CreateMemberRequest(String email, String name, String role, String password) {
}
