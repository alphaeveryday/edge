package com.edge.tenantconsole.service;

import com.edge.tenantconsole.dto.ExplanationResponse;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.repository.AnalysisItemStatusHistoryRepository;
import com.edge.tenantconsole.repository.ExplanationLedgerRepository;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.PublishedSummaryRepository;
import com.edge.tenantconsole.repository.ScreeningCheckRepository;
import com.edge.tenantconsole.repository.ScreeningRuleRepository;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.OffsetDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ExplanationEvidenceTest {

	private final ObjectMapper mapper = new ObjectMapper();

	private JsonNode response(String evidence) {
		var ledger = mock(ExplanationLedgerRepository.class);
		var item = mock(AnalysisItemEntity.class);
		when(item.getExplanationResultId()).thenReturn("analysis-1");
		when(item.getStatus()).thenReturn("APPROVED");
		when(item.getReceivedAt()).thenReturn(OffsetDateTime.parse("2026-10-02T10:00:00+09:00"));
		when(item.getExplanationAsOf()).thenReturn(OffsetDateTime.parse("2026-10-02T10:00:00+09:00"));
		when(item.getEvidences()).thenReturn("[" + evidence + "]");
		when(ledger.findById("analysis-1")).thenReturn(Optional.of(item));
		var service = new ExplanationService(ledger, mock(ScreeningCheckRepository.class),
				mock(ScreeningRuleRepository.class), mock(PublishedSummaryRepository.class),
				mock(PublicationRepository.class), mock(AnalysisItemStatusHistoryRepository.class),
				mock(ConsoleActionLogService.class));
		return mapper.valueToTree(ExplanationResponse.from(service.detail("analysis-1"))).get("evidence").get(0);
	}

	@Test
	void 계산_근거는_저장된_입출력과_수식을_그대로_전달한다() {
		var json = response("""
				{"kind":"CALCULATION","title":"기관 5일 순매수","source":"investor_flow_daily",
				 "published_at":null,"as_of":"2026-10-01","tool_run_id":"run-1","item_ids":["item-1"],
				 "arguments":{"days":5},"output":{"tool_run_id":"run-1","result":{"amount_krw":111000000}},
				 "formula_latex":"S=\\\\sum x_i","description":"최근 5거래일 기관 순매수 금액의 합계"}
				""");
		assertThat(json.get("type").asString()).isEqualTo("수치 계산");
		assertThat(json.get("time").asString()).isEqualTo("2026-10-01");
		assertThat(json.get("asOf").asString()).isEqualTo("2026-10-01");
		assertThat(json.get("toolRunId").asString()).isEqualTo("run-1");
		assertThat(json.get("itemIds")).isEqualTo(mapper.readTree("[\"item-1\"]"));
		assertThat(json.get("arguments")).isEqualTo(mapper.readTree("{\"days\":5}"));
		assertThat(json.get("output")).isEqualTo(mapper.readTree("{\"tool_run_id\":\"run-1\",\"result\":{\"amount_krw\":111000000}}"));
		assertThat(json.get("formulaLatex").asString()).isEqualTo("S=\\sum x_i");
		assertThat(json.get("description").asString()).isEqualTo("최근 5거래일 기관 순매수 금액의 합계");
	}

	@Test
	void 계산_시각은_관측시각을_사용하고_없으면_대체하지_않는다() {
		assertThat(response("{\"kind\":\"CALCULATION\",\"as_of\":\"2026-10-01T01:00:00Z\"}")
				.get("time").asString()).isEqualTo("2026-10-01 10:00 KST");
		assertThat(response("{\"kind\":\"CALCULATION\",\"published_at\":\"2026-10-02T01:00:00Z\",\"as_of\":null}")
				.get("time").asString()).isEqualTo("—");
	}

	@Test
	void 뉴스_ID를_유지하고_기존_뉴스에는_추가_필드를_만들지_않는다() {
		assertThat(response("{\"kind\":\"NEWS\",\"news_id\":\"news-1\",\"tool_run_id\":\"run-2\"}")
				.get("newsId").asString()).isEqualTo("news-1");
		var old = response("{\"kind\":\"NEWS\",\"title\":\"기사\",\"source\":\"bigkinds\",\"published_at\":null}");
		assertThat(old).isEqualTo(mapper.readTree("{\"type\":\"뉴스\",\"title\":\"기사\",\"source\":\"bigkinds\",\"time\":\"—\"}"));
	}
}
