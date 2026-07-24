package com.edge.screening.repository;

import com.edge.screening.service.BundleScreener;
import com.networknt.schema.InputFormat;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SchemaValidatorsConfig;
import com.networknt.schema.SpecVersion;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

/**
 * Consumer(wire) 측 계약 테스트 — 실제 wire 소비자 {@link BundleScreener}가 번들의
 * entry.evidences 를 analysis_item.evidences 로 옮길 때 계약 형상을 유지하는지 검증한다.
 *
 * WHY(Rule 9): BundleScreener 가 번들 evidences 를 analysis_item 에 적재하는 지점이라, 여기서
 * 키를 rename·drop·transform 하면 온프렘 화면이 조용히 빈다(ALPHA-395). schema 만 재검증하는
 * 테스트는 그걸 못 잡으므로, 실제 screen() 을 돌려 upsert 로 넘어가는 evidences 를 캡처해 계약과
 * 대조한다 — producer(tenant-sync-api)·parse(publication-api)와 같은 스키마를 로드해 양단 일치
 * 전 구간(직렬화→wire 적재→서빙 파싱)을 못박는다(ALPHA-497, edge-review C각도).
 */
class EventBundleContractTest {

	private final JsonSchema schema = loadSchema();

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

	@Test
	void BundleScreener_가_옮긴_evidences_가_계약형상을_유지한다() {
		PendingBundleRepository pending = mock(PendingBundleRepository.class);
		AnalysisItemRepository analysis = mock(AnalysisItemRepository.class);
		PublicationRepository publications = mock(PublicationRepository.class);
		BundleScreener screener = new BundleScreener(pending, analysis, publications);

		// 계약을 만족하는 populated evidences 를 담은 NEW 번들을 실제로 screen 한다
		String bundle = newBundleWith(
				"[{\"kind\":\"DISCLOSURE\",\"title\":\"삼성전자 공급계약 공시\",\"source\":\"DART\",\"published_at\":\"2026-07-14T09:00:00Z\"}]");
		assertThat(schema.validate(bundle, InputFormat.JSON)).as("입력 번들 자체가 계약을 만족해야 한다").isEmpty();

		screener.screen(101L, bundle.getBytes(StandardCharsets.UTF_8));

		// analysis_item 으로 넘어가는 evidencesJson(12번째 인자)을 캡처한다
		ArgumentCaptor<String> movedEvidences = ArgumentCaptor.forClass(String.class);
		verify(analysis).upsert(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
				movedEvidences.capture(), any(), any(), anyLong(), any());
		String moved = movedEvidences.getValue();

		// (1) 유실 금지 — 근거를 통째로 떨어뜨리면 안 된다
		assertThat(moved).as("BundleScreener 가 evidences 를 유실하면 안 된다").isNotEqualTo("[]");
		// (2) 형상 유지 — 옮긴 evidences 를 담은 번들이 다시 계약을 통과해야 한다(키 rename/transform 감지)
		assertThat(schema.validate(newBundleWith(moved), InputFormat.JSON))
				.as("BundleScreener 가 옮긴 evidences 가 계약 형상을 유지해야 한다: %s", moved).isEmpty();
	}

	private static String newBundleWith(String evidencesJson) {
		return ("""
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":101,
				 "entries":[{"cursor":101,"delivery_type":"NEW",
				   "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":"069500","etf_name":"KODEX 200","trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"EVENT_SUPPORTED","summary":"요약","confidence_level":"MEDIUM","primary_thread_id":"t1"},
				   "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				   "source_events":[],"evidences":%s}]}""").formatted(evidencesJson);
	}
}
