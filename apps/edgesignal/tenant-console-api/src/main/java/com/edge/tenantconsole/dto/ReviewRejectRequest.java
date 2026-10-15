package com.edge.tenantconsole.dto;

/**
 * 검수 반려 요청 — 사유(필수). ReviewController POST /api/v1/review/items/{id}/reject.
 * (평면 dto 패키지에서 ExplanationRejectRequest 와 구분하려 도메인 접두어를 붙였다.)
 */
public record ReviewRejectRequest(String reason) {
}
