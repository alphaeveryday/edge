package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.mock.ExplanationMockStore.Evidence;
import com.edge.tenantconsole.mock.ExplanationMockStore.Explanation;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * 가격 변동 설명 응답(ALPHA-513) — tenant-console-ui explanations 타입과 1:1 camelCase.
 * `final` 은 Java 예약어라 컴포넌트명은 finalText, JSON 은 @JsonProperty 로 맞춘다.
 * null 필드는 생략(UI optional 계약). mock record(Explanation/Evidence)와 형식이 같아도
 * 와이어 형은 별도 타입으로 둔다. 중첩 근거는 EvidenceResponse.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExplanationResponse(
		long id,
		String name,
		String code,
		String market,
		int direction,
		double changePct,
		String status,
		String risk,
		String reviewReason,
		String receivedRelative,
		String receivedAt,
		List<EvidenceResponse> evidence,
		String original,
		@JsonProperty("final") String finalText
) {
	public record EvidenceResponse(String type, String title, String source, String time) {

		public static EvidenceResponse from(Evidence e) {
			return new EvidenceResponse(e.type(), e.title(), e.source(), e.time());
		}
	}

	public static ExplanationResponse from(Explanation it) {
		return new ExplanationResponse(it.id(), it.name(), it.code(), it.market(),
				it.direction(), it.changePct(), it.status(), it.risk(), it.reviewReason(),
				it.receivedRelative(), it.receivedAt(),
				it.evidence().stream().map(EvidenceResponse::from).toList(),
				it.original(), it.finalText());
	}
}
