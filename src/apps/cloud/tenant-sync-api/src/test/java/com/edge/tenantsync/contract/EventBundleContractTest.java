package com.edge.tenantsync.contract;

import com.edge.tenantsync.dto.BundleEntry;
import com.edge.tenantsync.dto.EventBundle;
import com.edge.tenantsync.dto.ExplanationResult;
import com.edge.tenantsync.dto.ExplanationRun;
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
				"EVENT_SUPPORTED", "요약", "MEDIUM", "t1");
		ExplanationRun run = new ExplanationRun("run1", "v1");
		EventBundle bundle = EventBundle.of(1L, List.of(
				BundleEntry.newResult(101, result, run, List.of(), List.of()),
				BundleEntry.correction(102, "r0", "근거 공시 정정", result, run),
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
	void populated_flat_형상도_통과한다() {
		// ALPHA-363 조립 조인 구현 후 채워질 source_events·evidences(flat) 형상도 계약이 수용해야 한다.
		String populated = """
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":101,
				 "entries":[{"cursor":101,"delivery_type":"NEW",
				   "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":null,"etf_name":null,"trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"MIXED","summary":"s","confidence_level":null,"primary_thread_id":null},
				   "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				   "source_events":[{"source_event_id":"se1","source_class":"DISCLOSURE","event_type_code":"SUPPLY_CONTRACT","event_date":"2026-07-14"}],
				   "evidences":[{"kind":"DISCLOSURE","title":"삼성전자 공급계약 공시","source":"DART","published_at":"2026-07-14T09:00:00Z"}]}]}""";

		assertThat(schema.validate(populated, InputFormat.JSON))
				.as("363 후 populated flat 형상도 통과해야 한다").isEmpty();
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
