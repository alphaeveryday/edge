package com.edge.tenantconsole.model;

/** 자동 제공 기준(ALPHA-438) — policy_version 의 min_source_count·min_confidence 투영. */
public record AutoPublishCriteria(int minSources, String minConfidence) {
}
