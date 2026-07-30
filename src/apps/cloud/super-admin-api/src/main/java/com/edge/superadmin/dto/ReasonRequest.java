package com.edge.superadmin.dto;

/**
 * 사유만 담는 요청 — 분석 대상 제외/복원(POST /api/v1/analyses/{id}/exclude·restore)에 쓴다.
 * 제외 사유는 필수, 복원 사유는 선택이다(super-admin-console.md: 정정/무효화만 사유 필수).
 */
public record ReasonRequest(String reason) {
}
