package com.edge.superadmin.dto;

/**
 * 사유만 담는 요청 — 분석 대상 제외/복원/무효화(POST /api/v1/analyses/{id}/exclude·restore·
 * invalidate)에 쓴다. 제외·무효화 사유는 필수, 복원 사유는 선택이다(super-admin-console.md).
 */
public record ReasonRequest(String reason) {
}
