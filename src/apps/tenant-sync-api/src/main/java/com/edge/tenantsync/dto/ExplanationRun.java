package com.edge.tenantsync.dto;

/** 결과를 낸 실행 + 사용한 릴리스 번들 버전 — `explanation_run` 경계면. */
public record ExplanationRun(
		String explanationRunId,
		String releaseBundleVersion
) {
}
