package com.edge.tenantsync.dto;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.time.Instant;
import java.time.LocalDate;

/**
 * 고객 노출 후보 문구 — Cloud Event Store `explanation_result` 경계면
 * (docs/contracts/event-bundle-schema.md). 도메인 ID 는 Cloud 발번 TEXT.
 * 와이어 필드는 snake_case(중첩 record 라 @JsonNaming 별도 부착 필요).
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record ExplanationResult(
		String explanationResultId,
		String etfInstrumentId,
		String etfTicker,
		String etfName,
		LocalDate tradeDate,
		Instant explanationAsOf,
		String explanationType,
		String summary,
		String confidenceLevel,
		String primaryThreadId
) {
}
