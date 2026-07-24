package com.edge.tenantconsole.dto;

/**
 * 프로필 표시 이름 갱신 요청. ConsoleSessionController PATCH /api/v1/session/profile.
 */
public record ProfileRequest(String name) {
}
