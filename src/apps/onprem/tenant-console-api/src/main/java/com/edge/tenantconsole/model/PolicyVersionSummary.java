package com.edge.tenantconsole.model;

import java.time.Instant;

/** 정책 버전 이력 항목(ALPHA-438) — 발행 시각·발행자·활성 여부와 버전 설정 요약. */
public record PolicyVersionSummary(int versionNo, Instant publishedAt, String publishedBy,
		boolean active, boolean autoPublishEnabled, Integer minSources, String minConfidence) {
}
