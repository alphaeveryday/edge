package com.edge.intake.contract;

import com.networknt.schema.InputFormat;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Consumer 측 계약 테스트 — 온프렘 intake 가 수신하는 번들이 producer(tenant-sync-api)와
 * 동일한 와이어 계약(libs/schema/contracts/event-bundle.schema.json)을 따르는지 검증한다.
 * producer 테스트와 같은 스키마 파일을 로드하므로 "양단이 같은 계약"이 강제된다(ALPHA-497).
 *
 * WHY: 수신 번들 형상이 producer 와 어긋나면 raw 저장·다운스트림(publication-api 서빙)이
 * 조용히 깨진다(ALPHA-395). 이 테스트가 양단 계약 일치를 못박는다. (현행 BundleIngestor 는
 * 봉투 cursor 만 파싱하지만, 계약은 전체 형상을 정의한다 — 스키마 대조로 검증.)
 */
class EventBundleContractTest {

	private final JsonSchema schema = loadSchema();

	private static JsonSchema loadSchema() {
		JsonSchemaFactory factory = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012);
		try (InputStream in = EventBundleContractTest.class.getResourceAsStream("/event-bundle.schema.json")) {
			if (in == null) {
				throw new IllegalStateException("event-bundle.schema.json 이 테스트 classpath 에 없다 (build.gradle sourceSets)");
			}
			return factory.getSchema(in);
		} catch (java.io.IOException e) {
			throw new IllegalStateException(e);
		}
	}

	@Test
	void 수신_번들이_계약을_만족한다() {
		// producer 가 실제로 보내는 형상(현행 빈 배열 포함)
		String received = """
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":102,
				 "entries":[
				   {"cursor":101,"delivery_type":"NEW",
				    "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":"069500","etf_name":"KODEX 200","trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"EVENT_SUPPORTED","summary":"요약","confidence_level":"MEDIUM","primary_thread_id":"t1"},
				    "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				    "source_events":[],"evidences":[]},
				   {"cursor":102,"delivery_type":"INVALIDATION","target_explanation_result_id":"r0","reason":"오탐"}]}""";

		assertThat(schema.validate(received, InputFormat.JSON))
				.as("수신 번들이 양단 공유 계약을 통과해야 한다").isEmpty();
	}

	@Test
	void 계약위반_수신_번들은_거부된다() {
		// delivery_type 불명 — oneOf 어느 변형과도 일치하지 않는다.
		String bad = """
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":101,
				 "entries":[{"cursor":101,"delivery_type":"UNKNOWN","target_explanation_result_id":"r0","reason":"x"}]}""";

		assertThat(schema.validate(bad, InputFormat.JSON))
				.as("계약을 위반한 수신 번들은 거부되어야 한다").isNotEmpty();
	}
}
