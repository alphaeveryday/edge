package com.edge.publication.repository;

import com.edge.publication.repository.ExplanationStore.PublishedExplanation.Evidence;
import com.networknt.schema.InputFormat;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SchemaValidatorsConfig;
import com.networknt.schema.SpecVersion;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Consumer 측 계약 테스트 — 온프렘 실소비자(publication-api)가 producer(tenant-sync-api)와
 * 동일한 와이어 계약(libs/schema/contracts/event-bundle.schema.json)을 실제로 소비하는지 검증한다.
 *
 * WHY(Rule 9): schema 만 재검증하면 소비자 파싱이 어긋나도 못 잡는다(ALPHA-395 사고). 여기서는
 * (1) 계약이 evidences 형상을 수용하는지 + (2) 실제 파싱 코드 {@link ExplanationStore#parseEvidences}
 * 가 그 형상에서 kind·title·source·published_at 을 뽑아내는지 함께 못박는다 — 스키마 evidences 키와
 * 소비자 파싱 키가 드리프트하면 이 테스트가 깨진다(edge-review C각도). 같은 스키마 파일을 producer
 * 계약 테스트도 로드하므로 양단 일치가 성립한다(ALPHA-497).
 */
class EventBundleContractTest {

	private final JsonSchema schema = loadSchema();
	private final ExplanationStore store = new ExplanationStore(null, Set.of());

	private static JsonSchema loadSchema() {
		JsonSchemaFactory factory = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012);
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

	private static final String EVIDENCES =
			"[{\"kind\":\"DISCLOSURE\",\"title\":\"삼성전자 공급계약 공시\",\"source\":\"DART\",\"published_at\":\"2026-07-14T09:00:00Z\"}]";

	@Test
	void 계약형상_evidences_를_소비자가_그대로_파싱한다() {
		// (1) 이 evidences 형상이 계약(schema)을 만족하고
		assertThat(schema.validate(bundleWithEvidences(EVIDENCES), InputFormat.JSON))
				.as("evidences 샘플이 계약을 만족해야 한다").isEmpty();

		// (2) 실제 소비자 파싱이 계약 필드를 정확히 뽑아낸다 — 스키마 키와 파싱 키가 어긋나면 여기서 깨진다
		List<Evidence> parsed = store.parseEvidences(EVIDENCES);
		assertThat(parsed).singleElement().satisfies(e -> {
			assertThat(e.kind()).isEqualTo("DISCLOSURE");
			assertThat(e.title()).isEqualTo("삼성전자 공급계약 공시");
			assertThat(e.source()).isEqualTo("DART");
			assertThat(e.publishedAt()).isEqualTo(OffsetDateTime.parse("2026-07-14T09:00:00Z"));
		});
	}

	@Test
	void 현행_빈_배열_번들도_같은_스키마를_통과한다() {
		assertThat(schema.validate(bundleWithEvidences("[]"), InputFormat.JSON))
				.as("producer 현행 출력(빈 배열)도 양단 공유 계약을 통과해야 한다").isEmpty();
	}

	private static String bundleWithEvidences(String evidencesJson) {
		return ("""
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":101,
				 "entries":[{"cursor":101,"delivery_type":"NEW",
				   "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":null,"etf_name":null,"trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"MIXED","summary":"s","confidence_level":null,"primary_thread_id":null},
				   "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				   "source_events":[],"evidences":%s}]}""").formatted(evidencesJson);
	}
}
