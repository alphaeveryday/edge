package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.PolicyVersionSummary;

/** 정책 버전 이력 응답(ALPHA-438) — tenant-console-ui screening 도메인 계약과 1:1 camelCase. */
public record PolicyVersionResponse(int versionNo, String publishedAt, String publishedBy,
		boolean active, boolean autoPublishEnabled, Integer minSources, String minConfidence) {

	public static PolicyVersionResponse from(PolicyVersionSummary summary) {
		return new PolicyVersionResponse(summary.versionNo(),
				summary.publishedAt() == null ? null : summary.publishedAt().toString(),
				summary.publishedBy(), summary.active(), summary.autoPublishEnabled(),
				summary.minSources(), summary.minConfidence());
	}
}
