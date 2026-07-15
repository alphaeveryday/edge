package com.edge.tenantsync.dto;

import java.time.Instant;
import java.time.LocalDate;

/**
 * 고객 노출 후보 문구 — Cloud Event Store `explanation_result` 경계면
 * (docs/contracts/event-bundle-schema.md). 도메인 ID 는 Cloud 발번 TEXT.
 */
public record ExplanationResult(
		String explanationResultId,
		String etfInstrumentId,
		LocalDate tradeDate,
		Instant explanationAsOf,
		String explanationType,
		String summary,
		String confidenceLevel,
		String primaryThreadId
) {
}
