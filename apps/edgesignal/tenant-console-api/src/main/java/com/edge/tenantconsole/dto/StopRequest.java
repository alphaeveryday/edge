package com.edge.tenantconsole.dto;

/**
 * 제공 중단 요청 — 사유 필수(수동 중단 감사의 최소 단서, publication 스키마
 * unpublish_reason non-blank 제약과 정합). ExplanationController POST
 * /api/v1/explanations/{id}/stop.
 */
public record StopRequest(String reason) {
}
