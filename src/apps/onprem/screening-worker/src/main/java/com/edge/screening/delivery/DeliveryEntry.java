package com.edge.screening.delivery;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * 번들 entry 의 typed VO(안티커럽션 계층, ADR-0039 §2) — JSON 은 파서에서 1회 파싱·계약
 * 검증되고, 이후 판정·적재는 이 형상만 본다. delivery_type 별 필수 필드(NEW 의
 * explanation_result, INVALIDATION 의 target)는 프로토콜 분기 소관이라
 * 오케스트레이터(BundleScreener)가 검증한다.
 */
public record DeliveryEntry(long cursor, String deliveryType, ExplanationResult explanationResult,
		String targetExplanationResultId, String reason, String evidencesJson, int sourceEventCount) {

	/** explanation_result 페이로드 — 필드 정의 SSOT 는 docs/contracts/event-bundle-schema.md. */
	public record ExplanationResult(String explanationResultId, String etfInstrumentId, String etfTicker,
			String etfName, LocalDate tradeDate, OffsetDateTime explanationAsOf, String explanationType,
			String summary, String headline, String confidenceLevel, String primaryThreadId) {
	}
}
