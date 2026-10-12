package com.edge.screening.repository;

import com.edge.screening.entity.PolicyVersion;
import com.edge.screening.service.BundleScreener;
import com.edge.screening.delivery.DeliveryBundleParser;
import com.networknt.schema.InputFormat;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SchemaValidatorsConfig;
import com.networknt.schema.SpecVersion;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.ObjectMapper;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

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
	private final ObjectMapper mapper = new ObjectMapper();

	private static final String CALCULATION = """
			{"kind":"CALCULATION","title":"외국인 최근 5거래일 순매수 합계",
			 "source":"일별 투자자 수급","published_at":null,"as_of":"2026-07-14",
			 "tool_run_id":"calc-1","item_ids":["item-1","item-2"],
			 "arguments":{"investor":"foreign","days":5},
			 "output":{"tool_run_id":"calc-1","result":{"amount_krw":1300000000,"precise":"0.000123456789"}},
			 "formula_latex":"S = N_1 + N_2 + N_3 + N_4 + N_5",
			 "description":"지정한 5거래일의 외국인 순매수 금액을 합산합니다."}
			""";
	private static final String NEWS = """
			{"kind":"NEWS","title":"장비 공급 계약","source":"뉴스 공급자","published_at":null,
			 "news_id":"news-1","tool_run_id":"news-run-1","item_ids":["item-1"]}
			""";

	@Test
	void 계산근거의_저장값과_관측일이_계약을_만족한다() {
		assertThat(schema.validate(newBundleWith("[" + CALCULATION + "]"), InputFormat.JSON)).isEmpty();
	}

	@Test
	void 뉴스ID는_뉴스에만_붙인다() {
		assertThat(schema.validate(newBundleWith("[" + NEWS + "]"), InputFormat.JSON)).isEmpty();
		assertThat(schema.validate(newBundleWith("[" + NEWS.replace("\"NEWS\"", "\"DISCLOSURE\"") + "]"),
				InputFormat.JSON)).isNotEmpty();
	}

	@Test
	void 기준일과_수식이_없으면_추측하지않고_null로_보낸다() {
		var node = (tools.jackson.databind.node.ObjectNode) mapper.readTree(CALCULATION);
		node.putNull("as_of");
		node.putNull("formula_latex");
		assertThat(schema.validate(newBundleWith("[" + node + "]"), InputFormat.JSON)).isEmpty();
	}

	@Test
	void 계산의_기준일을_기사발표시각에_넣지않는다() {
		var node = (tools.jackson.databind.node.ObjectNode) mapper.readTree(CALCULATION);
		node.put("published_at", "2026-07-15T10:00:00+09:00");
		assertThat(schema.validate(newBundleWith("[" + node + "]"), InputFormat.JSON)).isNotEmpty();
	}

	@ParameterizedTest
	@ValueSource(strings = {"tool_run_id", "arguments", "output", "formula_latex", "description", "as_of", "item_ids"})
	void 불완전한_계산근거는_계약에서_거부한다(String field) {
		var node = (tools.jackson.databind.node.ObjectNode) mapper.readTree(CALCULATION);
		node.remove(field);
		assertThat(schema.validate(newBundleWith("[" + node + "]"), InputFormat.JSON)).isNotEmpty();
	}

	@ParameterizedTest
	@ValueSource(strings = {"2026-07-14", "2026-07-15T10:00:00+09:00"})
	void 날짜와_오프셋시각을_관측기준으로_허용한다(String asOf) {
		var node = (tools.jackson.databind.node.ObjectNode) mapper.readTree(CALCULATION);
		node.put("as_of", asOf);
		assertThat(schema.validate(newBundleWith("[" + node + "]"), InputFormat.JSON)).isEmpty();
	}

	@ParameterizedTest
	@ValueSource(strings = {"어제", "2026-07-15T10:00:00", "2026-02-30"})
	void 모호하거나_존재하지않는_관측일은_거부한다(String asOf) {
		var node = (tools.jackson.databind.node.ObjectNode) mapper.readTree(CALCULATION);
		node.put("as_of", asOf);
		assertThat(schema.validate(newBundleWith("[" + node + "]"), InputFormat.JSON)).isNotEmpty();
	}

	@Test
	void 계산근거를_추가해도_심사정책의_출처수가_늘지않는다() {
		var entries = new DeliveryBundleParser().parse(101L,
				envelope(newBundleWith("[" + CALCULATION + "]")).getBytes(StandardCharsets.UTF_8));
		assertThat(entries.getFirst().sourceEventCount()).isZero();
		assertThat(mapper.readTree(entries.getFirst().evidencesJson()).get(0)).isEqualTo(mapper.readTree(CALCULATION));
	}

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

	@ParameterizedTest
	@ValueSource(booleans = {false, true})
	void BundleScreener_가_옮긴_evidences_가_계약형상을_유지한다(boolean includeCalculation) {
		PendingBundleRepository pending = mock(PendingBundleRepository.class);
		AnalysisItemRepository analysis = mock(AnalysisItemRepository.class);
		PublicationRepository publications = mock(PublicationRepository.class);
		PolicyRepository policies = mock(PolicyRepository.class);
		ScreeningRuleRepository rules = mock(ScreeningRuleRepository.class);
		ScreeningCheckRepository checks = mock(ScreeningCheckRepository.class);
		AnalysisItemStatusHistoryRepository history = mock(AnalysisItemStatusHistoryRepository.class);
		// NEW 판정은 활성 정책이 전제 — 관대한 정책(자동 제공 ON·룰 없음)으로 통과시킨다.
		when(policies.findActive()).thenReturn(Optional.of(new PolicyVersion(1L, true, null, null)));
		// upsert 1행(신규 수신)이어야 판정·근거 기록 경로가 실행된다 — mock 기본값 0은 재수신 skip 이 된다.
		when(analysis.upsert(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
				any(), anyLong(), any(), any(), any())).thenReturn(1);
		BundleScreener screener = new BundleScreener(pending, analysis, publications, policies, rules, checks, history);

		// 계약을 만족하는 populated evidences 를 담은 NEW 번들을 실제로 screen 한다
		// (source_uri 포함 — optional 확장 필드(ALPHA-739)도 wire 적재에서 보존돼야 한다)
		String bundle = newBundleWith(
				"[{\"kind\":\"DISCLOSURE\",\"title\":\"삼성전자 공급계약 공시\",\"source\":\"DART\",\"published_at\":\"2026-07-14T09:00:00Z\","
						+ "\"source_uri\":\"https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260714000001\"}]");
		if (includeCalculation) {
			bundle = bundle.replace("\"evidences\":[", "\"evidences\":[" + CALCULATION + "," + NEWS + ",");
		}
		assertThat(schema.validate(bundle, InputFormat.JSON)).as("입력 번들 자체가 계약을 만족해야 한다").isEmpty();

		// 저장 body 는 신형 봉투(ADR-0040 T4) — 스키마 검증은 봉투 안 EventBundle(bundle) 대상이고,
		// 파서 입력만 봉투로 감싼다.
		screener.screen(101L, envelope(bundle).getBytes(StandardCharsets.UTF_8));

		// analysis_item 으로 넘어가는 evidencesJson(12번째)·content_as_of(15번째) 인자를 캡처한다
		ArgumentCaptor<String> movedEvidences = ArgumentCaptor.forClass(String.class);
		ArgumentCaptor<java.time.OffsetDateTime> movedContentAsOf =
				ArgumentCaptor.forClass(java.time.OffsetDateTime.class);
		verify(analysis).upsert(any(), any(), any(), any(), any(), any(), any(), any(), any(), any(), any(),
				movedEvidences.capture(), anyLong(), any(), movedContentAsOf.capture(), any());
		String moved = movedEvidences.getValue();
		assertThat(mapper.readTree(moved))
				.isEqualTo(mapper.readTree(bundle).path("entries").get(0).path("evidences"));
		// optional content_as_of(ALPHA-918)도 번들→원장 배선에서 유실되면 안 된다 —
		// 파서가 null 로 흘리거나 upsert 전달이 빠지면 여기서 잡힌다.
		assertThat(movedContentAsOf.getValue())
				.isEqualTo(java.time.OffsetDateTime.parse("2026-07-15T10:30:00+09:00"));

		// (1) 유실 금지 — 근거를 통째로 떨어뜨리면 안 된다
		assertThat(moved).as("BundleScreener 가 evidences 를 유실하면 안 된다").isNotEqualTo("[]");
		// (2) 형상 유지 — 옮긴 evidences 를 담은 번들이 다시 계약을 통과해야 한다(키 rename/transform 감지)
		assertThat(schema.validate(newBundleWith(moved), InputFormat.JSON))
				.as("BundleScreener 가 옮긴 evidences 가 계약 형상을 유지해야 한다: %s", moved).isEmpty();
		// (3) optional 확장 필드 보존 — source_uri 는 스키마상 optional 이라 (2)의 재검증으로는
		// drop 을 못 잡는다. 콘솔 원문 링크(ALPHA-739)의 공급이 여기서 끊기면 안 된다.
		assertThat(moved).as("optional source_uri 도 유실하면 안 된다(ALPHA-739)").contains("source_uri");
	}

	/** 신형 와이어 형상(ADR-0040 T4 후 유일 형상) — EventBundle 을 ApiResponse 봉투(result 아래)로 감싼다. */
	private static String envelope(String innerBundleJson) {
		return "{\"isSuccess\":true,\"code\":\"COMMON200\",\"message\":\"성공\",\"result\":" + innerBundleJson + "}";
	}

	private static String newBundleWith(String evidencesJson) {
		return ("""
				{"bundle_id":"0198aaaa-bbbb-cccc-dddd-eeeeeeeeeeee","tenant_id":1,
				 "generated_at":"2026-07-15T09:00:00Z","cursor_from":101,"cursor_to":101,
				 "entries":[{"cursor":101,"delivery_type":"NEW",
				   "explanation_result":{"explanation_result_id":"r1","etf_instrument_id":"i1","etf_ticker":"069500","etf_name":"KODEX 200","trade_date":"2026-07-15","explanation_as_of":"2026-07-15T09:00:00Z","explanation_type":"EVENT_SUPPORTED","summary":"요약","confidence_level":"MEDIUM","primary_thread_id":"t1","content_as_of":"2026-07-15T10:30:00+09:00"},
				   "explanation_run":{"explanation_run_id":"run1","release_bundle_version":"v1"},
				   "source_events":[],"evidences":%s}]}""").formatted(evidencesJson);
	}
}
