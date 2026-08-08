package com.edge.publication.dto;

import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 200 응답 — docs/contracts/publication-api.md 형상(snake_case) 그대로.
 * summary 는 검수를 거친 최종 노출 문구(원천: explanation_result.summary),
 * disclaimer 는 화면에 반드시 함께 노출한다 — 콘솔이 발행한 활성 정책 버전의 면책 문구다.
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record ExplanationResponse(
		String publicationId,
		EtfInfo etf,
		LocalDate tradeDate,
		String summary,
		String confidenceLevel,
		List<EvidenceItem> evidences,
		String disclaimer,
		OffsetDateTime publishedAt,
		/** 스냅샷 기준시각 — 재노출(fallback)분의 기준 시점을 화면이 알 수 있게(ADR-0045 결정 3). */
		OffsetDateTime explanationAsOf
) {

	public record EtfInfo(String ticker, String name) {
	}

	@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
	public record EvidenceItem(String kind, String title, String source, OffsetDateTime publishedAt) {
	}
}
