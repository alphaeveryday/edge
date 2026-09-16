package com.edge.superadmin.dto;

import com.edge.superadmin.repository.PipelineStatusRepository.GridCell;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.JsonNode;

import static org.assertj.core.api.Assertions.assertThat;

class NewsQualityAssessmentTest {
	private final ObjectMapper mapper = new ObjectMapper();

	private JsonNode assertion(long total, long resolved, long excluded, long technical) {
		return mapper.readTree("""
				{"schema":"news_resolution_v1","scope":"assertion_arguments","metrics":
				{"total":%d,"resolved":%d,"unresolved":%d,"excludedAssertions":%d,"technicalFailures":%d}}
				""".formatted(total, resolved, total - resolved, excluded, technical));
	}

	private JsonNode event(long total, long anchorless) {
		return mapper.readTree("""
				{"schema":"news_resolution_v1","scope":"anchorless_events",
				"metrics":{"events":%d,"anchorless":%d,"unresolvedArguments":100}}
				""".formatted(total, anchorless));
	}

	private GridCell cell(String task, long out, long failed, JsonNode diagnostic) {
		return new GridCell("feature", task, "DUE", "FULFILLED", failed > 0 ? "INCOMPLETE" : "UNKNOWN",
				out, null, failed, null, null, false, diagnostic, true, false);
	}

	@Test
	void 미해소_한_건이_아닌_운영_비율로_판정한다() {
		var a = NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 1278, 88, assertion(3354, 2278, 88, 0)));
		assertThat(a.status()).isEqualTo("WITHIN_LIMITS");
		assertThat(a.resolutionRate()).isEqualTo(2278.0 / 3354);
		assertThat(a.exclusionRate()).isEqualTo(88.0 / 1366);
		assertThat(NewsQualityAssessment.from(cell("ASSEMBLE_EVENTS", 296, 24, event(296, 24))).status())
				.isEqualTo("WITHIN_LIMITS");
	}

	@Test
	void 해소율_60퍼센트와_제외율_20퍼센트_경계를_지킨다() {
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 999, 1, assertion(1000, 599, 1, 0))).status()).isEqualTo("CAUTION");
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 999, 1, assertion(1000, 600, 1, 0))).status()).isEqualTo("WITHIN_LIMITS");
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 801, 199, assertion(1000, 900, 199, 0))).status()).isEqualTo("WITHIN_LIMITS");
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 800, 200, assertion(1000, 900, 200, 0))).reason()).isEqualTo("EXCLUSION_RATE");
		assertThat(NewsQualityAssessment.from(cell("ASSEMBLE_EVENTS", 1000, 199, event(1000, 199))).status()).isEqualTo("WITHIN_LIMITS");
		assertThat(NewsQualityAssessment.from(cell("ASSEMBLE_EVENTS", 1000, 200, event(1000, 200))).status()).isEqualTo("CAUTION");
	}

	@Test
	void 기술_오류는_비율과_무관하게_한_건부터_주의다() {
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 9999, 2, assertion(9999, 9000, 1, 1))).reason()).isEqualTo("TECHNICAL_FAILURE");
		assertThat(NewsQualityAssessment.from(cell("ASSEMBLE_EVENTS", 9999, 2, event(9999, 1))).reason()).isEqualTo("TECHNICAL_FAILURE");
	}

	@Test
	void 계측_누락이나_모순과_빈_분모를_정상으로_추정하지_않는다() {
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 1278, 88,
				mapper.readTree("""
				{"schema":"news_resolution_v1","scope":"assertion_arguments","metrics":{"total":3354,"resolved":2278,"unresolved":1076}}
				"""))).status()).isEqualTo("UNMEASURED");
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 10, 2, assertion(10, 9, 1, 0))).status()).isEqualTo("UNMEASURED");
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 0, 0, assertion(0, 0, 0, 0))).status()).isEqualTo("UNMEASURED");
		assertThat(NewsQualityAssessment.from(cell("LOAD_ASSERTIONS", 0, 10, assertion(0, 0, 10, 0))).status()).isEqualTo("CAUTION");
		assertThat(NewsQualityAssessment.from(cell("ASSEMBLE_EVENTS", 10, 1, event(11, 1))).status()).isEqualTo("UNMEASURED");
		assertThat(NewsQualityAssessment.from(cell("ASSEMBLE_EVENTS", 0, 0, event(0, 0))).status()).isEqualTo("UNMEASURED");
	}

	@Test
	void 성공한_현재시도의_근거만_쓰고_실제_누락을_숨기지_않는다() {
		var evidence = assertion(100, 90, 1, 0);
		for (String outcome : new String[] {"FAILED", "MISSED", "BLOCKED", "PENDING"}) {
			assertThat(NewsQualityAssessment.from(new GridCell("feature", "LOAD_ASSERTIONS", "DUE", outcome,
					"INCOMPLETE", 99L, null, 1L, null, null, false, evidence, true, false)).status()).isEqualTo("UNMEASURED");
		}
		assertThat(NewsQualityAssessment.from(new GridCell("feature", "LOAD_ASSERTIONS", "DUE", "FULFILLED",
				"INCOMPLETE", 99L, null, 1L, null, null, false, evidence, false, false)).status()).isEqualTo("UNMEASURED");
		assertThat(NewsQualityAssessment.from(new GridCell("feature", "LOAD_ASSERTIONS", "DUE", "FULFILLED",
				"INCOMPLETE", 99L, null, 1L, null, null, false, evidence, true, true)).reason()).isEqualTo("DATA_ERROR");
		assertThat(NewsQualityAssessment.from(cell("TAG_NEWS", 10, 1, evidence))).isNull();
	}
}
