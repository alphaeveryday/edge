package com.edge.tenantsync.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.util.List;

/** 저장된 계산 근거. 결과를 재계산하거나 결측 관측시각을 실행시각으로 대체하지 않는다. */
@JsonInclude(JsonInclude.Include.ALWAYS)
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record CalculationEvidenceItem(
		String title,
		String source,
		String asOf,
		String toolRunId,
		List<String> itemIds,
		JsonNode arguments,
		JsonNode output,
		String formulaLatex,
		String description
) implements BundleEvidence {

	@JsonProperty("kind")
	public String kind() {
		return "CALCULATION";
	}

	@JsonProperty("published_at")
	public String publishedAt() {
		return null;
	}
}
