package com.edge.tenantsync.contract;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.BundleEvidence;
import com.edge.tenantsync.dto.CalculationEvidenceItem;
import com.edge.tenantsync.dto.EventBundle;
import com.edge.tenantsync.dto.EvidenceItem;
import com.edge.tenantsync.dto.ExplanationResult;
import com.edge.tenantsync.dto.ExplanationRun;
import com.edge.tenantsync.dto.SourceEventItem;
import com.networknt.schema.InputFormat;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SchemaValidatorsConfig;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.io.InputStream;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Producer 측 계약 테스트 — tenant-sync-api 가 직렬화한 번들이 와이어 계약
 * (libs/schema/contracts/event-bundle.schema.json)을 만족하는지 검증한다. consumer(intake)
 * 테스트가 같은 스키마 파일을 쓰므로 "양단이 같은 계약"이 강제된다(ALPHA-497).
 *
 * WHY: 계약(schema)이 producer 실제 출력·소비자 파싱 형상과 어긋나면 온프렘 화면이
 * 조용히 빈칸이 된다(ALPHA-395). 이 테스트가 직렬화 형상을 계약에 못박는다.
 */
class EventBundleContractTest {

	private final JsonSchema schema = loadSchema();

	@Test
	void v2는_엔진을_명시하고_v1_판정값을_만들지_않는다() {
		var result = new ExplanationResult("v2-result", "i1", "091160", "KODEX 반도체",
				LocalDate.parse("2026-10-02"), Instant.parse("2026-10-02T01:00:00Z"),
				null, "설명", null, null, Instant.parse("2026-10-02T01:00:00Z"), "v2");
		var bundle = EventBundle.of(1L, List.of(BundleEntry.newResult(1, result,
				new ExplanationRun("v2-result", "v2"), List.of(), List.of())));
		ObjectMapper mapper = new ObjectMapper();
		String json = mapper.writeValueAsString(bundle);
		assertThat(schema.validate(json, InputFormat.JSON)).isEmpty();
		assertThat(mapper.readTree(json).path("entries").get(0).path("explanation_result")
				.path("analysis_engine").asString()).isEqualTo("v2");
		assertThat(schema.validate(json.replace("\"v2\"", "\"v3\""), InputFormat.JSON)).isNotEmpty();
		assertThat(schema.validate(json.replace("\"confidence_level\":null", "\"confidence_level\":\"HIGH\""), InputFormat.JSON)).isNotEmpty();
		assertThat(schema.validate(json.replace("\"explanation_type\":null", "\"explanation_type\":\"MIXED\""), InputFormat.JSON)).isNotEmpty();
		assertThat(schema.validate(json.replace("\"analysis_engine\":\"v2\"", "\"analysis_engine\":\"v1\""), InputFormat.JSON)).isNotEmpty();
	}

	@Test
	void 뉴스와_계산의_실제_DTO를_번들에_섞어도_감사값이_보존된다() {
		ObjectMapper mapper = new ObjectMapper();
		var arguments = mapper.readTree("{\"investor\":\"foreign\",\"days\":5}");
		var output = mapper.readTree("""
				{"tool_run_id":"calc-1","result":{"amount_krw":9007199254740993,
				 "ratio":"1.50","series":{"columns":["day","value"],"rows":[["2026-09-30",18]]}}}
				""");
		var calculation = new CalculationEvidenceItem("외국인 순매수", "investor_flow_daily",
				"2026-09-30", "calc-1", List.of("item-1", "item-2"), arguments, output,
				"\\sum_{d=1}^{5} x_d", "선택한 거래일의 외국인 순매수 금액을 합산합니다.");
		var news = new EvidenceItem("NEWS", "기사 제목", "bigkinds", "2026-09-30T00:00:00Z",
				null, "news-1", "news-run-1", List.of("item-1"));
		String json = serializeEvidence(List.of(news, calculation));
		assertThat(schema.validate(json, InputFormat.JSON)).isEmpty();
		var items = mapper.readTree(json).path("entries").get(0).path("evidences");
		assertThat(items.get(0).path("news_id").asText()).isEqualTo("news-1");
		assertThat(items.get(0).path("tool_run_id").asText()).isEqualTo("news-run-1");
		assertThat(items.get(0).path("item_ids")).isEqualTo(mapper.readTree("[\"item-1\"]"));
		assertThat(items.get(0).has("arguments")).isFalse();
		var actual = items.get(1);
		assertThat(actual.path("kind").asText()).isEqualTo("CALCULATION");
		assertThat(actual.path("tool_run_id").asText()).isEqualTo("calc-1");
		assertThat(actual.path("arguments")).isEqualTo(arguments);
		assertThat(actual.path("output")).isEqualTo(output);
		assertThat(actual.path("formula_latex").asText()).isEqualTo(calculation.formulaLatex());
		assertThat(actual.path("item_ids").size()).isEqualTo(2);
		assertThat(actual.path("as_of").asText()).isEqualTo("2026-09-30");
		assertThat(actual.path("published_at").isNull()).isTrue();
		assertThat(actual.has("news_id")).isFalse();
	}

	@Test
	void 관측시점과_수식을_모르면_필수_null_필드를_그대로_전달한다() {
		ObjectMapper mapper = new ObjectMapper();
		var calculation = new CalculationEvidenceItem("수급", "investor_flow_daily", null,
				"calc-1", List.of("item-1"), mapper.readTree("{}"), mapper.readTree("{}"),
				null, "외국인 순매수 금액입니다.");
		String json = serializeEvidence(List.of(calculation));
		assertThat(schema.validate(json, InputFormat.JSON)).isEmpty();
		var item = mapper.readTree(json).path("entries").get(0).path("evidences").get(0);
		for (String field : List.of("published_at", "as_of", "formula_latex")) {
			assertThat(item.has(field)).as(field).isTrue();
			assertThat(item.path(field).isNull()).as(field).isTrue();
		}
	}

	@Test
	void 기존_문서는_추가_감사필드를_null로_보내지_않는다() {
		String json = serializeEvidence(List.of(new EvidenceItem("DISCLOSURE", null, "DART", null, null)));
		assertThat(schema.validate(json, InputFormat.JSON)).isEmpty();
		var item = new ObjectMapper().readTree(json).path("entries").get(0).path("evidences").get(0);
		assertThat(item.size()).isEqualTo(5);
		for (String field : List.of("news_id", "tool_run_id", "item_ids")) {
			assertThat(item.has(field)).as(field).isFalse();
		}
	}

	private static String serializeEvidence(List<? extends BundleEvidence> evidence) {
		var result = new ExplanationResult("r1", "i1", "069500", "KODEX 200",
				LocalDate.parse("2026-09-30"), Instant.parse("2026-09-30T01:00:00Z"),
				"MIXED", "요약", null, null, null);
		return new ObjectMapper().writeValueAsString(EventBundle.of(1L, List.of(
				BundleEntry.newResult(1, result, new ExplanationRun("run1", "v1"), List.of(), evidence))));
	}

	private static JsonSchema loadSchema() {
		JsonSchemaFactory factory = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012);
		// format(uuid·date·date-time)을 annotation 이 아니라 assertion 으로 강제한다 — 켜지 않으면
		// 잘못된 published_at 이 계약을 통과해 소비자 OffsetDateTime.parse 에서 크래시한다.
		SchemaValidatorsConfig config = SchemaValidatorsConfig.builder().formatAssertionsEnabled(true).build();
		try (InputStream in = EventBundleContractTest.class.getResourceAsStream("/event-bundle.schema.json")) {
			if (in == null) {
				throw new IllegalStateException("event-bundle.schema.json 이 테스트 classpath 에 없다 (build.gradle sourceSets)");
			}
			return factory.getSchema(in, config);
		} catch (java.io.IOException e) {
			throw new IllegalStateException(e);
		}
	}

	@Test
	void 직렬화된_번들이_계약을_만족한다() {
		ExplanationResult result = new ExplanationResult("r1", "i1", "069500", "KODEX 200",
				LocalDate.parse("2026-07-15"), Instant.parse("2026-07-15T09:00:00Z"),
				"EVENT_SUPPORTED", "요약", "MEDIUM", "t1",
				Instant.parse("2026-07-15T01:30:00Z"));
		ExplanationRun run = new ExplanationRun("run1", "v1");
		// source_events·evidences 는 실 조립 형상(SourceEventItem·EvidenceItem)으로 싣는다
		// (ALPHA-718) — event_date·title·published_at 이 null 이어도 키 자체는 required 라
		// 직렬화에서 생략되면 계약 위반으로 여기서 잡힌다. lineage 없는 NEW(빈 배열)도 함께
		// 직렬화한다 — include 정책이 NON_EMPTY 로 바뀌어 빈 배열 키가 생략되는 회귀를 잡는다.
		EventBundle bundle = EventBundle.of(1L, List.of(
				BundleEntry.newResult(101, result, run, List.of(
						new SourceEventItem("se1", "NEWS", "EARNINGS", "2026-07-14"),
						new SourceEventItem("se2", "DISCLOSURE", "SUPPLY_CONTRACT", null)), List.of(
						new EvidenceItem("NEWS", "실적 발표 기사", "YONHAP", "2026-07-14T00:00:00Z",
								"https://news.example.com/a1"),
						new EvidenceItem("DISCLOSURE", null, "DART", null, null))),
				BundleEntry.newResult(102, result, run, List.of(), List.of()),
				BundleEntry.invalidation(103, "r0", "오탐지 이벤트")));

		// @JsonNaming 가드레일: BundleSerializer 제거(ADR-0040) 후엔 DTO의 @JsonNaming 이 유일한 snake_case
		// 소스다. bare ObjectMapper(=production Spring mapper와 동일 경로) 직렬화가 스키마를 만족해야 한다.
		// @JsonNaming 이 빠지면 camelCase 가 나와 required(bundle_id 등) 위반으로 이 테스트가 실패한다.
		String json = new ObjectMapper().writeValueAsString(bundle);

		Set<ValidationMessage> errors = schema.validate(json, InputFormat.JSON);
		assertThat(errors).as("직렬화된 번들이 계약 스키마를 통과해야 한다: %s", errors).isEmpty();
	}

	@Test
	void 계약위반_번들은_거부된다() {
		// INVALIDATION 은 대상·사유만 담는다 — explanation_result 를 실으면 additionalProperties:false 로 거부.
		String bad = """
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":103,"cursor_to":103,
				 "entries":[{"cursor":103,"delivery_type":"INVALIDATION","target_explanation_result_id":"r0",
				   "reason":"오탐","explanation_result":{"explanation_result_id":"x"}}]}""";

		assertThat(schema.validate(bad, InputFormat.JSON))
				.as("계약을 위반한 번들(INVALIDATION+result)은 거부되어야 한다").isNotEmpty();
	}

	@Test
	void 폐지된_CORRECTION_형상은_계약에서_거부된다() {
		// ADR-0044 — 전달 유형은 NEW·INVALIDATION 2형상뿐이다. 구 CORRECTION 형상(대상·사유
		// + 재게시 본체)이 계약을 통과하면 폐지가 와이어에서 강제되지 않는다.
		String correction = """
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":102,"cursor_to":102,
				 "entries":[{"cursor":102,"delivery_type":"CORRECTION","target_explanation_result_id":"r0",
				   "reason":"근거 공시 정정",
				   "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":null,"etf_name":null,"trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"MIXED","summary":"s","confidence_level":null,"primary_thread_id":null},
				   "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				   "source_events":[],"evidences":[]}]}""";

		assertThat(schema.validate(correction, InputFormat.JSON))
				.as("폐지된 CORRECTION 형상은 거부되어야 한다(ADR-0044)").isNotEmpty();
	}

	@Test
	void populated_flat_형상도_통과한다() {
		// 조립 조인(ALPHA-718)이 채우는 source_events·evidences(flat) 형상을 계약이 수용해야 한다.
		String populated = """
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":101,
				 "entries":[{"cursor":101,"delivery_type":"NEW",
				   "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":null,"etf_name":null,"trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"MIXED","summary":"s","confidence_level":null,"primary_thread_id":null},
				   "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				   "source_events":[{"source_event_id":"se1","source_class":"DISCLOSURE","event_type_code":"SUPPLY_CONTRACT","event_date":"2026-07-14"}],
				   "evidences":[{"kind":"DISCLOSURE","title":"삼성전자 공급계약 공시","source":"DART","published_at":"2026-07-14T09:00:00Z"}]}]}""";

		assertThat(schema.validate(populated, InputFormat.JSON))
				.as("populated flat 형상도 통과해야 한다(ALPHA-718)").isEmpty();
	}

	@Test
	void 근거의_미지_키는_거부된다() {
		// EvidenceItem 은 additionalProperties: false 다 — source_url 같은 오타 키가 조용히
		// 수용되면 소비자(콘솔 파서)는 결측으로 읽어 링크가 소리 없이 사라진다(ALPHA-739).
		String unknownKey = """
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":101,
				 "entries":[{"cursor":101,"delivery_type":"NEW",
				   "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":null,"etf_name":null,"trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"MIXED","summary":"s","confidence_level":null,"primary_thread_id":null},
				   "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				   "source_events":[],
				   "evidences":[{"kind":"DISCLOSURE","title":"공시","source":"DART","published_at":null,"source_url":"https://dart.fss.or.kr/x"}]}]}""";

		assertThat(schema.validate(unknownKey, InputFormat.JSON))
				.as("EvidenceItem 의 미지 키는 거부되어야 한다(additionalProperties: false)").isNotEmpty();
	}

	@Test
	void 잘못된_date_time_포맷은_거부된다() {
		// evidences[].published_at 이 date-time 이 아니면 계약에서 거부되어야 한다 — 통과시키면
		// 소비자(publication-api ExplanationStore) 의 OffsetDateTime.parse 가 크래시한다.
		String badFormat = """
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":101,
				 "entries":[{"cursor":101,"delivery_type":"NEW",
				   "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":null,"etf_name":null,"trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"MIXED","summary":"s","confidence_level":null,"primary_thread_id":null},
				   "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				   "source_events":[],
				   "evidences":[{"kind":"DISCLOSURE","title":"공시","source":"DART","published_at":"어제"}]}]}""";

		assertThat(schema.validate(badFormat, InputFormat.JSON))
				.as("date-time 포맷 위반은 거부되어야 한다(format assertion)").isNotEmpty();
	}
}
