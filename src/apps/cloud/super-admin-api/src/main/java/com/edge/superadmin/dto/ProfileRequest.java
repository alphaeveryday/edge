package com.edge.superadmin.dto;

/**
 * 프로필 표시 이름 변경 요청. AdminSessionController PATCH /api/v1/session/profile.
 */
public record ProfileRequest(String name) {
}
