package com.edge.tenantconsole.dto;

/**
 * 로그인 요청 — 이메일·비밀번호. AuthController POST /api/v1/auth/login.
 */
public record LoginRequest(String email, String password) {
}
