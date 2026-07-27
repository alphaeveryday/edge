package com.edge.tenantsync.repository;

import java.time.Instant;
import java.time.LocalDate;

/**
 * 조회 프로젝션 한 행 — SELECT 목록과 1:1(JPQL constructor expression 대상).
 * 와이어 매핑·분기(delivery_type)는 BundleEntryStore 몫이라 여기엔 로직이 없다.
 * INVALIDATION 행은 본체 조인이 비므로 result·run 계열 필드가 null 이다.
 */
public record DeliveryRow(
		long cursor,
		String deliveryType,
		String targetExplanationResultId,
		String reason,
		String explanationResultId,
		String etfInstrumentId,
		String etfTicker,
		String etfName,
		LocalDate tradeDate,
		Instant explanationAsOf,
		String explanationType,
		String summary,
		String confidenceLevel,
		String primaryThreadId,
		String explanationRunId,
		String bundleVersion) {
}
