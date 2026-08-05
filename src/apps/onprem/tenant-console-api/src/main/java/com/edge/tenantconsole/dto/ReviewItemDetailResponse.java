package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.ReviewItem;
import com.edge.tenantconsole.model.ReviewItemDetail;
import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.util.List;

/**
 * 검수 상세 응답(ALPHA-436, 구 439 흡수) — snake_case(검수 표면 규약). evidences 는
 * 경계면 계약 형상([{kind,title,source,published_at,source_uri}])을 그대로 통과시킨다. 검사
 * 결과·상태 이력이 감사 열람의 원천이다(전용 Audit 메뉴 없음 — 콘솔 IA).
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ReviewItemDetailResponse(
		String explanationResultId,
		String etfTicker,
		String etfName,
		String tradeDate,
		String summary,
		String headline,
		String confidenceLevel,
		String status,
		String receivedAt,
		JsonNode evidences,
		List<String> reviewReasons,
		List<CheckResponse> checks,
		List<HistoryResponse> history
) {

	@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
	@JsonInclude(JsonInclude.Include.NON_NULL)
	/** 검사 한 행 — policyVersionNo·minSourceCount·minConfidence 는 판정 당시 값이다(ALPHA-774). */
	public record CheckResponse(String result, String ruleType, String matchedText,
			String checkedAt, Integer policyVersionNo, Integer minSourceCount,
			String minConfidence) {
	}

	@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
	@JsonInclude(JsonInclude.Include.NON_NULL)
	public record HistoryResponse(String fromStatus, String toStatus, String actorType,
			String actorName, String reason, String occurredAt) {
	}

	public static ReviewItemDetailResponse from(ReviewItemDetail detail) {
		ReviewItem i = detail.item();
		return new ReviewItemDetailResponse(
				i.explanationResultId(), i.etfTicker(), i.etfName(),
				i.tradeDate() == null ? null : i.tradeDate().toString(),
				i.summary(), i.headline(), i.confidenceLevel(), i.status(),
				i.receivedAt() == null ? null : i.receivedAt().toString(),
				detail.evidences(),
				detail.reviewReasons(),
				detail.checks().stream()
						.map(c -> new CheckResponse(c.result(), c.ruleType(), c.matchedText(),
								c.checkedAt() == null ? null : c.checkedAt().toString(),
								c.policyVersionNo(), c.minSourceCount(), c.minConfidence()))
						.toList(),
				detail.history().stream()
						.map(h -> new HistoryResponse(h.fromStatus(), h.toStatus(), h.actorType(),
								h.actorName(), h.reason(),
								h.occurredAt() == null ? null : h.occurredAt().toString()))
						.toList());
	}
}
