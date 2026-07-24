package com.edge.tenantconsole.dto;

/**
 * 설명 반려 요청 — 비고. ExplanationController POST /api/v1/explanations/{id}/reject.
 * (평면 dto 패키지에서 ReviewRejectRequest 와 구분하려 도메인 접두어를 붙였다.)
 */
public record ExplanationRejectRequest(String note) {
}
