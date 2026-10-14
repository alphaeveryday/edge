package com.edge.superadmin.dto;

import com.edge.superadmin.repository.AnalysisResultRepository.Detail;
import com.edge.superadmin.repository.AnalysisResultRepository.Movement;
import com.edge.superadmin.repository.AnalysisResultRepository.Outlook;
import tools.jackson.databind.JsonNode;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 새 분석 엔진 분석 한 건(ALPHA-1333). 저장된 내용을 그대로 싣는다. 종류마다 한 갈래만 값이 있고
 * 다른 갈래는 null 이다 — 전망이면 {@code outlook}, 가격변동이면 {@code movement}.
 */
public record AnalysisResultDetailResponse(String analysisId, String kind, String etfCode,
		String etfName, OffsetDateTime analysisAt, String status, String dataSource, boolean published,
		OffsetDateTime publishedAt, String previousAnalysisId, String errorMessage,
		OutlookResponse outlook, MovementResponse movement) {

	public record OutlookResponse(String sticker, String summaryTitle, String summary,
			String detailTitle, String detailMode, String conclusionTitle, String conclusionSentence,
			String changeCondition, String issueHeadline, List<OutlookItemResponse> items,
			List<FactorResponse> factors, List<FactorMetricResponse> factorMetrics,
			List<IssueItemResponse> issueItems, List<KeywordResponse> conclusionKeywords) {

		static OutlookResponse from(Outlook o) {
			return new OutlookResponse(o.sticker(), o.summaryTitle(), o.summary(), o.detailTitle(),
					o.detailMode(), o.conclusionTitle(), o.conclusionSentence(), o.changeCondition(),
					o.issueHeadline(), o.items().stream().map(i -> new OutlookItemResponse(i.itemId(), i.section(),
							i.changeType(), i.position(), i.titleKeyword(), i.bullets(), i.sentence(),
							i.sentiment(), i.sourceLinks(), i.sourceAsOf(), i.toolRunIds())).toList(),
					o.factors().stream().map(f -> new FactorResponse(f.type(), f.sticker(),
							f.sentence())).toList(),
					o.factorMetrics().stream().map(m -> new FactorMetricResponse(m.factorType(),
							m.metricKey(), m.numericValue(), m.textValue(), m.observedDate(),
							m.observedAt(), m.subject(), m.position(), m.toolRunIds())).toList(),
					o.issueItems().stream().map(i -> new IssueItemResponse(i.position(),
							i.titleKeyword(), i.sentence(), i.sentiment(), i.toolRunIds())).toList(),
					o.conclusionKeywords().stream().map(k -> new KeywordResponse(k.kind(),
							k.position(), k.label(), k.toolRunIds())).toList());
		}
	}

	public record OutlookItemResponse(String itemId, String section, String changeType, int position,
			String titleKeyword, JsonNode bullets, String sentence, String sentiment,
			JsonNode sourceLinks, OffsetDateTime sourceAsOf, List<String> toolRunIds) {
	}

	public record FactorResponse(String type, String sticker, String sentence) {
	}

	public record FactorMetricResponse(String factorType, String metricKey, BigDecimal numericValue,
			String textValue, LocalDate observedDate, OffsetDateTime observedAt, String subject,
			int position, List<String> toolRunIds) {
	}

	public record IssueItemResponse(int position, String titleKeyword, String sentence,
			String sentiment, List<String> toolRunIds) {
	}

	public record KeywordResponse(String kind, int position, String label, List<String> toolRunIds) {
	}

	public record MovementResponse(LocalDate tradingDate, String summary, List<String> selectedItemIds,
			boolean withdrawn, OffsetDateTime withdrawnAt, List<MovementItemResponse> items) {

		static MovementResponse from(Movement m) {
			return new MovementResponse(m.tradingDate(), m.summary(), m.selectedItemIds(),
					m.withdrawn(), m.withdrawnAt(), m.items().stream().map(i ->
							new MovementItemResponse(i.itemId(), i.type(), i.titleKeyword(),
									i.sentence(), i.sentiment(), i.sourceAsOf(), i.toolRunIds()))
							.toList());
		}
	}

	public record MovementItemResponse(String itemId, String type, String titleKeyword,
			String sentence, String sentiment, OffsetDateTime sourceAsOf, List<String> toolRunIds) {
	}

	public static AnalysisResultDetailResponse from(Detail d) {
		return new AnalysisResultDetailResponse(d.analysisId(), d.kind(), d.etfCode(), d.etfName(),
				d.analysisAt(), d.status(), d.dataSource(), d.published(), d.publishedAt(),
				d.previousAnalysisId(), d.errorMessage(),
				d.outlook() == null ? null : OutlookResponse.from(d.outlook()),
				d.movement() == null ? null : MovementResponse.from(d.movement()));
	}
}
