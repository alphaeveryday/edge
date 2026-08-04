package com.edge.publication;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.publication.controller.ExplanationController;
import com.edge.publication.service.ExplanationService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 콘솔 제공 범위 토글(serving_scope)의 서빙단 실효화(ALPHA-614)를 실 Postgres 로 검증한다.
 * WHY: 토글이 저장만 되고 서빙이 이를 읽지 않으면 이해상충 제외가 고객 노출을 통제하지 못한다 —
 * 판정은 게시분 조회 앞단에서 걸러 "설명 없음"(204·Exposure 미기록)으로 수렴해야 하고, 상위
 * (MARKET) 차단이 하위(INSTRUMENT) 토글에 우선해야 한다.
 *
 * <p>이 테스트만 게시분 조회 캐시(ALPHA-433 ExplanationStore serveCache)를 끈다(TTL 0s) —
 * 제공 범위 판정은 그 캐시 <b>앞단</b>이라 기능적으로 무관하나, 메서드마다 시드를 지우고 다시
 * 넣어 publication_id 가 바뀌므로 캐시된 이전 게시분이 재조회를 오염시키지 않도록 결정론을 확보한다.
 */
class ExplanationScopeIntegrationTest extends OnpremPostgresIntegrationTest {

	private static final String TICKER = "069500";
	private static final LocalDate TRADE_DATE = LocalDate.of(2026, 7, 15);

	@DynamicPropertySource
	static void disableServeCache(DynamicPropertyRegistry registry) {
		registry.add("publication.serve-cache-ttl", () -> "0s");
	}

	@Autowired
	private ExplanationService explanationService;

	@Autowired
	private JdbcTemplate jdbc;

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		jdbc.update("DELETE FROM exposure_log");
		jdbc.update("DELETE FROM serving_scope");
		jdbc.update("DELETE FROM publication");
		jdbc.update("DELETE FROM analysis_item");
		seedPublishedExplanation();
		mvc = MockMvcBuilders
				.standaloneSetup(new ExplanationController(explanationService))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void scope_행이_없으면_기본_제공이라_200이고_Exposure가_기록된다() throws Exception {
		// WHY: 옵트아웃 모델 — 토글 이력이 없는 종목은 종전처럼 노출돼야 한다(판정 도입이 기본 제공을 깨면 안 된다).
		serve().andExpect(status().isOk()).andExpect(jsonPath("$.etf.ticker").value(TICKER));
		assertThat(exposureCount()).isEqualTo(1);
	}

	@Test
	void INSTRUMENT_토글_OFF면_204이고_Exposure는_기록되지_않는다() throws Exception {
		// WHY: 종목 제외가 곧 고객 노출 차단이어야 한다 — 게시분이 있어도 204 로 수렴하고, 노출이 없었으니 기록도 없어야 감사 수치가 정확하다.
		insertScope("INSTRUMENT", TICKER, false);
		serve().andExpect(status().isNoContent());
		assertThat(exposureCount()).isZero();
	}

	@Test
	void INSTRUMENT_토글이_재개enabled_true면_다시_200이다() throws Exception {
		// WHY: 재개(enabled=true) 행은 행 부재와 같게 제공으로 돌아와야 한다 — 차단이 비가역이면 운영이 불가능하다.
		insertScope("INSTRUMENT", TICKER, true);
		serve().andExpect(status().isOk());
		assertThat(exposureCount()).isEqualTo(1);
	}

	@Test
	void MARKET_XKRX_OFF는_종목이_enabled여도_전역_차단이라_204다() throws Exception {
		// WHY: 상위(MARKET) 차단이 하위(INSTRUMENT) 토글에 우선한다 — KRX 단일 유니버스 전제(ADR-0024)의 전역 스위치라 종목 ON 을 무시하고 차단해야 한다.
		insertScope("MARKET", "XKRX", false);
		insertScope("INSTRUMENT", TICKER, true);
		serve().andExpect(status().isNoContent());
		assertThat(exposureCount()).isZero();
	}

	@Test
	void 미상장_코드는_판정_이전에_404다() throws Exception {
		// WHY: 404(미상장)와 204(차단·설명 없음)는 다른 질문이다 — 제공 범위 판정 도입이 상장 여부 계약(SERV4040)을 흔들면 안 된다.
		mvc.perform(get("/api/v1/explanations/999999")
						.header("X-Customer-Hash", "hash-1").header("X-Channel", "MTS"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("SERV4040"));
	}

	private ResultActions serve() throws Exception {
		return mvc.perform(get("/api/v1/explanations/" + TICKER)
				.header("X-Customer-Hash", "hash-1").header("X-Channel", "MTS"));
	}

	private int exposureCount() {
		return jdbc.queryForObject("SELECT count(*) FROM exposure_log", Integer.class);
	}

	private void insertScope(String scopeType, String scopeKey, boolean enabled) {
		jdbc.update("INSERT INTO serving_scope (scope_type, scope_key, enabled) VALUES (?, ?, ?)",
				scopeType, scopeKey, enabled);
	}

	private void seedPublishedExplanation() {
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker, etf_name,
				    trade_date, explanation_as_of, explanation_type, summary, confidence_level, status, evidences)
				VALUES (?, ?, ?, ?, ?, now(), 'PRICE_ONLY', ?, 'MEDIUM', 'AUTO_PUBLISHED', NULL)
				""", "a-scope", "instr-a-scope", TICKER, "KODEX 200", TRADE_DATE, "요약 a-scope");
		jdbc.update("""
				INSERT INTO publication (analysis_item_id, etf_ticker, trade_date,
				                         explanation_as_of, status)
				VALUES (?, ?, ?, ?::date + time '16:00' at time zone 'Asia/Seoul', 'PUBLISHED')
				""", "a-scope", TICKER, TRADE_DATE, TRADE_DATE);
	}
}
