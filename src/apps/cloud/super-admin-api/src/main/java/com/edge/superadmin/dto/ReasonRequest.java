package com.edge.superadmin.dto;

/**
 * 사유만 담는 요청 — 분석 무효화(POST /api/v1/analyses/{id}/invalidate)에 쓴다. 사유는
 * 필수다(super-admin-console.md). 구 제외/복원 표면은 ALPHA-737 로 은퇴했다.
 */
public record ReasonRequest(String reason) {
}
