package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.ReviewListEntry;
import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * 검수 대기 항목 응답 — Review Queue 형상(state-machine.md). 필드는 snake_case,
 * null 필드는 생략. review_reasons 는 screening_check 파생(ALPHA-436). 도메인 record 와 형식이 같아도 와이어 형은 별도 타입.
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ReviewItemResponse(
		String explanationResultId,
		String etfTicker,
		String etfName,
		String tradeDate,
		String summary,
		String headline,
		String confidenceLevel,
		String status,
		String supersedesItemId,
		String correctionReason,
		String receivedAt,
		java.util.List<String> reviewReasons
) {
	public static ReviewItemResponse from(ReviewListEntry entry) {
		var i = entry.item();
		return new ReviewItemResponse(
				i.explanationResultId(), i.etfTicker(), i.etfName(),
				i.tradeDate() == null ? null : i.tradeDate().toString(),
				i.summary(), i.headline(), i.confidenceLevel(), i.status(),
				i.supersedesItemId(), i.correctionReason(),
				i.receivedAt() == null ? null : i.receivedAt().toString(),
				entry.reviewReasons());
	}
}
