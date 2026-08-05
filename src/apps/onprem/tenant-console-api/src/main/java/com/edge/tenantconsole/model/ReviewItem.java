package com.edge.tenantconsole.model;

import com.edge.tenantconsole.entity.AnalysisItemEntity;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * 검수 항목 도메인 표현 — 서비스가 소비하고 dto(ReviewItemResponse)가 매핑하는 계층
 * (영속 엔티티와 분리). 리포지토리가 반환한 AnalysisItemEntity 를 서비스가 이 record 로
 * 매핑한다(엔티티는 영속 계층에 머문다).
 */
public record ReviewItem(
		String explanationResultId,
		String etfTicker,
		String etfName,
		LocalDate tradeDate,
		String summary,
		String headline,
		String confidenceLevel,
		String status,
		OffsetDateTime receivedAt,
		/** 스냅샷 기준시각 — 승인 게시 시 publication grain 축으로 복사(ALPHA-743). */
		OffsetDateTime explanationAsOf
) {
	public static ReviewItem from(AnalysisItemEntity e) {
		return new ReviewItem(e.getExplanationResultId(), e.getEtfTicker(), e.getEtfName(),
				e.getTradeDate(), e.getSummary(), e.getHeadline(), e.getConfidenceLevel(),
				e.getStatus(), e.getReceivedAt(), e.getExplanationAsOf());
	}
}
