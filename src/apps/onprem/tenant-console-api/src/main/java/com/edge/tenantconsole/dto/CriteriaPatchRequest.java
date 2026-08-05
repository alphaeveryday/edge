package com.edge.tenantconsole.dto;

/**
 * 자동 제공 기준 부분 갱신 요청 — PATCH 시맨틱이라 null 필드는 유지한다.
 * ScreeningController PATCH /api/v1/screening/criteria.
 */
public record CriteriaPatchRequest(Boolean autoPublishEnabled, Integer minSources,
		String minConfidence) {
}
