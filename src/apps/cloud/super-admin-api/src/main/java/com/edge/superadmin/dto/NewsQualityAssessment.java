package com.edge.superadmin.dto;

import com.edge.superadmin.repository.PipelineStatusRepository.GridCell;
import tools.jackson.databind.JsonNode;

import java.math.BigDecimal;

/** 실행이력의 운영 판정. 원장의 INCOMPLETE/failed_records는 그대로 보존한다. */
public record NewsQualityAssessment(String status, String reason,
		Double resolutionRate, Double exclusionRate,
		Long policyExcluded, Double assessmentResolutionRate, String assessmentBasis) {

	public static NewsQualityAssessment from(GridCell cell) {
		boolean assertions = "LOAD_ASSERTIONS".equals(cell.taskKey());
		if (!assertions && !"ASSEMBLE_EVENTS".equals(cell.taskKey())) return null;
		if (!"DUE".equals(cell.planStatus()) || !"FULFILLED".equals(cell.outcome())
				|| cell.running() || !cell.qualityEvidenceCurrent()) return unknown();
		if ("INVALID".equals(cell.dataStatus()) || cell.completenessGap()) {
			return result("CAUTION", "DATA_ERROR", null, null);
		}
		if (!"INCOMPLETE".equals(cell.dataStatus()) && !"UNKNOWN".equals(cell.dataStatus())
				&& !"VALID".equals(cell.dataStatus()) && !"VALID_EMPTY".equals(cell.dataStatus())) return unknown();
		JsonNode diagnostic = cell.qualityDiagnostics();
		if (diagnostic == null || !"news_resolution_v1".equals(diagnostic.path("schema").asText())
				|| !(assertions ? "assertion_arguments" : "anchorless_events")
						.equals(diagnostic.path("scope").asText())
				|| !count(cell.recordsOut()) || !count(cell.failedRecords())) return unknown();
		JsonNode metrics = diagnostic.path("metrics");
		if (assertions) return assertions(cell, metrics);
		Long events = metric(metrics, "events"), anchorless = metric(metrics, "anchorless");
		if (events == null || anchorless == null || anchorless > events
				|| !events.equals(cell.recordsOut()) || anchorless > cell.failedRecords()) return unknown();
		Double excluded = events == 0 ? null : (double) anchorless / events;
		// 성공한 이벤트 실행의 잔여 제외는 stage_rejected(허용되지 않은 단계 값)다.
		if (cell.failedRecords() > anchorless) return result("CAUTION", "DATA_ERROR", null, excluded);
		if (events == 0 || ("INCOMPLETE".equals(cell.dataStatus()) && anchorless == 0)) return unknown();
		return result(atLeastTwentyPercent(anchorless, BigDecimal.valueOf(events)) ? "CAUTION" : "WITHIN_LIMITS",
				"ANCHORLESS_RATE", null, excluded);
	}

	private static NewsQualityAssessment assertions(GridCell cell, JsonNode metrics) {
		Long total = metric(metrics, "total"), resolved = metric(metrics, "resolved"),
				unresolved = metric(metrics, "unresolved"), excluded = metric(metrics, "excludedAssertions"),
				technical = metric(metrics, "technicalFailures");
		if (total == null || resolved == null || unresolved == null || resolved > total
				|| unresolved != total - resolved) return unknown();
		Double resolution = total == 0 ? null : (double) resolved / total;
		// 이전 진단에는 제외/기술 오류 구분이 없다. 해소율만으로 기술 오류를 숨기지 않는다.
		if (excluded == null || technical == null || technical > cell.failedRecords()
				|| excluded != cell.failedRecords() - technical) {
			return result("UNMEASURED", "INSUFFICIENT_EVIDENCE", resolution, null);
		}
		BigDecimal considered = BigDecimal.valueOf(cell.recordsOut()).add(BigDecimal.valueOf(excluded));
		Double exclusion = considered.signum() == 0 ? null : excluded / considered.doubleValue();
		if (technical > 0) return result("CAUTION", "TECHNICAL_FAILURE", resolution, exclusion);
		if (considered.signum() > 0 && atLeastTwentyPercent(excluded, considered)) {
			return result("CAUTION", "EXCLUSION_RATE", resolution, exclusion);
		}
		if (total == 0 || considered.signum() == 0
				|| ("INCOMPLETE".equals(cell.dataStatus()) && excluded == 0)) return unknown();
		boolean policyPresent = metrics.has("policyExcluded") || metrics.has("actionableUnresolved");
		Long policy = policyPresent ? metric(metrics, "policyExcluded") : Long.valueOf(0);
		Long actionable = policyPresent ? metric(metrics, "actionableUnresolved") : unresolved;
		if (policy == null || actionable == null || policy > unresolved || actionable != unresolved - policy) {
			return result("UNMEASURED", "INSUFFICIENT_EVIDENCE", resolution, exclusion);
		}
		long denominator = total - policy;
		String basis = policyPresent ? "POLICY_ADJUSTED" : "LEGACY";
		Double assessed = denominator == 0 ? null : (double) resolved / denominator;
		if (denominator == 0) return new NewsQualityAssessment("UNMEASURED", "INSUFFICIENT_EVIDENCE",
				resolution, exclusion, policyPresent ? policy : null, null, basis);
		// 기존 뉴스 추이 하한 60%(ALPHA-1003). 건수 곱셈은 bigint overflow 없이 비교한다.
		boolean low = BigDecimal.valueOf(resolved).multiply(BigDecimal.valueOf(5))
				.compareTo(BigDecimal.valueOf(denominator).multiply(BigDecimal.valueOf(3))) < 0;
		return new NewsQualityAssessment(low ? "CAUTION" : "WITHIN_LIMITS", "RESOLUTION_RATE",
				resolution, exclusion, policyPresent ? policy : null, assessed, basis);
	}

	private static boolean atLeastTwentyPercent(long excluded, BigDecimal total) {
		return BigDecimal.valueOf(excluded).multiply(BigDecimal.valueOf(5)).compareTo(total) >= 0;
	}

	private static boolean count(Long value) {
		return value != null && value >= 0;
	}

	private static Long metric(JsonNode metrics, String name) {
		JsonNode value = metrics.path(name);
		return value.isIntegralNumber() && value.canConvertToLong() && value.longValue() >= 0
				? value.longValue() : null;
	}

	private static NewsQualityAssessment unknown() {
		return result("UNMEASURED", "INSUFFICIENT_EVIDENCE", null, null);
	}

	private static NewsQualityAssessment result(String status, String reason, Double resolution, Double exclusion) {
		return new NewsQualityAssessment(status, reason, resolution, exclusion, null, null, null);
	}
}
