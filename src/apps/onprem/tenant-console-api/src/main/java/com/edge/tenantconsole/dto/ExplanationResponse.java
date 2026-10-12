package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.Explanation;
import com.edge.tenantconsole.support.TimeText;
import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import tools.jackson.databind.JsonNode;

/**
 * 가격 변동 설명 응답(ALPHA-607 실전환) — tenant-console-ui explanations 타입과 1:1
 * camelCase. `final` 은 Java 예약어라 컴포넌트명은 finalText, JSON 은 @JsonProperty 로 맞춘다.
 * null 필드는 생략(UI optional 계약). 원장 도메인(model.Explanation)을 UI 어휘로 번역만
 * 한다: 근거 kind→공시/뉴스, 시각→KST 표시 문자열.
 *
 * <p>market·direction·changePct 는 온프렘 원장에 없어(ALPHA-497 이연) 축소 계약에서 빠졌다
 * (사용자 결정 2026-07-29) — materialization 후 복원한다.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record ExplanationResponse(
		String id,
		String name,
		String code,
		String status,
		String confidence,
		String reviewReason,
		String receivedRelative,
		String receivedAt,
		String explanationAsOf,
		String contentAsOf,
		boolean serving,
		List<EvidenceResponse> evidence,
		String original,
		@JsonProperty("final") String finalText
) {
	// NON_NULL 은 중첩 record 에 상속되지 않는다 — sourceUri 결측(EOD 구멍)을 키 생략으로
	// 내보내려면(UI optional 계약) 여기 명시해야 한다.
	@JsonInclude(JsonInclude.Include.NON_NULL)
	public record EvidenceResponse(String type, String title, String source, String time,
			String sourceUri, String newsId, String toolRunId, JsonNode itemIds, String asOf,
			JsonNode arguments, JsonNode output, String formulaLatex, String description) {

		public static EvidenceResponse from(Explanation.Evidence e) {
			String type = switch (e.kind() == null ? "" : e.kind()) {
				case "NEWS" -> "뉴스";
				case "DISCLOSURE" -> "공시";
				case "CALCULATION" -> "수치 계산";
				default -> e.kind();
			};
			// title·source 는 NULL 허용 — UI 계약(title·source: string)을 깨지 않게 폴백한다.
			// sourceUri 는 링크라 폴백 없이 null 통과(NON_NULL 생략) — UI 가 링크 미표시로 처리.
			return new EvidenceResponse(type, e.title() == null ? "(제목 없음)" : e.title(),
					e.source() == null ? "(출처 없음)" : e.source(),
					"CALCULATION".equals(e.kind()) ? observationTime(e.asOf()) : TimeText.doc(e.publishedAt()),
					e.sourceUri(), e.newsId(), e.toolRunId(), e.itemIds(), e.asOf(),
					e.arguments(), e.output(), e.formulaLatex(), e.description());
		}

		private static String observationTime(String value) {
			if (value == null) return "—";
			try {
				return value.length() == 10 ? LocalDate.parse(value).toString()
						: TimeText.doc(OffsetDateTime.parse(value));
			} catch (java.time.format.DateTimeParseException e) {
				// 잘못된 원값은 asOf 에 남겨 진단 가능하게 하고 가짜 표시 시각을 만들지 않는다.
				return "—";
			}
		}
	}

	public static ExplanationResponse from(Explanation it) {
		return new ExplanationResponse(it.id(), it.name(), it.code(), it.status(), it.confidence(),
				it.reviewReason(), TimeText.relative(it.receivedAt()), TimeText.absolute(it.receivedAt()),
				// 기준시각(ALPHA-744) — 원장 explanation_as_of 는 NOT NULL 이라 폴백 불요
				TimeText.absolute(it.explanationAsOf()),
				// 콘텐츠 기준시각(ALPHA-918) — nullable: 결측이면 NON_NULL 로 키 생략(UI 가 as_of 폴백)
				it.contentAsOf() == null ? null : TimeText.absolute(it.contentAsOf()),
				it.serving(),
				it.evidence().stream().map(EvidenceResponse::from).toList(),
				it.original(), it.finalText());
	}
}
