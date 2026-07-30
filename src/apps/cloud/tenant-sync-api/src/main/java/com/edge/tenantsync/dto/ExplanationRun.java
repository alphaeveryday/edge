package com.edge.tenantsync.dto;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/** 결과를 낸 실행 + 사용한 릴리스 번들 버전 — `explanation_run` 경계면. 와이어 필드 snake_case. */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record ExplanationRun(
		String explanationRunId,
		String releaseBundleVersion
) {
}
