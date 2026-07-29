package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.AbstractPostgresIntegrationTest;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.model.MarketScope;
import com.edge.tenantconsole.model.StockScope;
import com.edge.tenantconsole.service.ScopeService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 제공 범위 실전환(ALPHA-606)의 DB 계약을 실 Postgres 로 검증한다 — 손수 대역이 우회하는
 * 실제 의미가 WHY(Rule 9): serving_scope 옵트아웃 모델(행 부재 = 기본 제공)과 토글
 * upsert(첫 토글 = enabled=false 삽입, 재토글 = 반전, UNIQUE(scope_type, scope_key)로 1행
 * 유지), 시장의 MIC(XKRX) 저장, updated_by 의 member FK, 그리고 종목 유니버스가
 * analysis_item 실조회(ticker 미결측만)라는 계약. serving_scope 는 이 기능 전유라 테스트마다
 * 전체 비운다.
 */
class ScopeIT extends AbstractPostgresIntegrationTest {

	@Autowired
	private ScopeService scope;
	@Autowired
	private MemberRepository members;
	@Autowired
	private JdbcTemplate jdbc;

	private long actorId;

	@BeforeEach
	void isolate() {
		jdbc.update("DELETE FROM serving_scope");
		actorId = members.save(new MemberEntity(
				"it606-" + System.nanoTime() + "@demo.edge.local", "관리자", "TENANT_ADMIN", null))
				.getMemberId();
	}

	private void seedItem(String id, String ticker, String name) {
		seedItemAt(id, ticker, name, "now()", "now()");
	}

	private void seedItemAt(String id, String ticker, String name, String receivedAtSql,
			String asOfSql) {
		// analysis_item NOT NULL 컬럼만 채운 최소 행 — 유니버스 조회 대상. received_at·
		// explanation_as_of 는 티커별 최신 이름 선택(정렬·tie-breaker) 검증을 위해 명시한다.
		jdbc.update("""
				INSERT INTO analysis_item (explanation_result_id, etf_instrument_id, etf_ticker,
				    etf_name, trade_date, explanation_as_of, explanation_type, summary, status, received_at)
				VALUES (?, ?, ?, ?, DATE '2026-07-22', %s, 'PRICE_ONLY', '요약', 'AUTO_PUBLISHED', %s)
				""".formatted(asOfSql, receivedAtSql), id, "inst_" + ticker, ticker, name);
	}

	@Test
	void 종목_유니버스는_코드와_이름이_모두_있는_항목만_조회한다() {
		String nonce = String.valueOf(System.nanoTime());
		seedItem("er-" + nonce + "-a", "TICK" + nonce, "테스트 ETF " + nonce);
		seedItem("er-" + nonce + "-noticker", null, null);          // ticker 결측 — 제외
		seedItem("er-" + nonce + "-noname", "NONM" + nonce, null);  // 이름 결측 — 제외(UI 계약상 name 비-null)

		List<StockScope> stocks = scope.listStocks();
		assertThat(stocks).anySatisfy(s -> {
			assertThat(s.code()).isEqualTo("TICK" + nonce);
			assertThat(s.name()).isEqualTo("테스트 ETF " + nonce);
			assertThat(s.market()).isEqualTo("KRX");   // ADR-0024 MVP — 온프렘엔 시장 분류 컬럼 없음
			assertThat(s.enabled()).isTrue();          // 토글 이력 없음 = 옵트아웃 기본 제공
		});
		// 이름 결측 항목은 목록에도, 토글 대상에도 없다(유니버스 술어 일치).
		assertThat(stocks).noneSatisfy(s -> assertThat(s.name()).isNull());
		assertThat(stocks).noneSatisfy(s -> assertThat(s.code()).isEqualTo("NONM" + nonce));
	}

	@Test
	void 같은_티커는_최신_수신_이름으로_유니버스에_한_번_나온다() {
		// WHY: 같은 티커가 이름이 갱신되며 여러 번 수신될 수 있다 — DISTINCT ON 이 최신
		// 수신 행을 골라야 콘솔이 낡은 종목명을 보여주지 않는다. 정렬이 ASC 로 뒤집히거나
		// tie-breaker 가 빠지면 이 단언이 깨진다(Rule 9 — 반례를 실제로 거부).
		String nonce = String.valueOf(System.nanoTime());
		String ticker = "TICK" + nonce;
		seedItemAt("er-" + nonce + "-old", ticker, "구 이름 " + nonce,
				"now() - interval '1 hour'", "now() - interval '1 hour'");
		seedItemAt("er-" + nonce + "-new", ticker, "새 이름 " + nonce, "now()", "now()");

		List<StockScope> forTicker = scope.listStocks().stream()
				.filter(s -> s.code().equals(ticker)).toList();
		assertThat(forTicker).hasSize(1);
		assertThat(forTicker.get(0).name()).isEqualTo("새 이름 " + nonce);
	}

	@Test
	void 수신_시각이_같으면_explanation_as_of_최신_이름을_고른다() {
		// WHY: 한 번들의 여러 행은 received_at(now())이 같다 — 이때 explanation_as_of·PK
		// tie-breaker 가 없거나 뒤집히면 이름 선택이 비결정적이 된다. 같은 received_at 에
		// as_of 만 다르게 넣어 tie-breaker 가 최신을 고르는지 검증한다(Rule 9 — 반례 거부).
		String nonce = String.valueOf(System.nanoTime());
		String ticker = "TIE" + nonce;
		String sameReceived = "timestamptz '2026-07-22 09:00:00+09'";
		seedItemAt("er-" + nonce + "-old", ticker, "구 이름 " + nonce,
				sameReceived, "timestamptz '2026-07-22 08:00:00+09'");
		seedItemAt("er-" + nonce + "-new", ticker, "새 이름 " + nonce,
				sameReceived, "timestamptz '2026-07-22 08:30:00+09'");

		List<StockScope> forTicker = scope.listStocks().stream()
				.filter(s -> s.code().equals(ticker)).toList();
		assertThat(forTicker).hasSize(1);
		assertThat(forTicker.get(0).name()).isEqualTo("새 이름 " + nonce);
	}

	@Test
	void 종목_토글은_옵트아웃_행을_upsert하고_재토글은_반전한다() {
		String nonce = String.valueOf(System.nanoTime());
		String ticker = "TICK" + nonce;
		seedItem("er-" + nonce, ticker, "테스트 ETF");

		// 첫 토글 — 기본 제공(행 부재)에서 제외로: enabled=false 행 1건 + updated_by 기록.
		scope.toggleStock(ticker, actorId);
		List<Map<String, Object>> rows = jdbc.queryForList(
				"SELECT enabled, updated_by FROM serving_scope WHERE scope_type = 'INSTRUMENT' AND scope_key = ?",
				ticker);
		assertThat(rows).hasSize(1);
		assertThat(rows.get(0).get("enabled")).isEqualTo(false);
		assertThat(rows.get(0).get("updated_by")).isEqualTo(actorId);
		assertThat(currentStock(ticker).enabled()).isFalse();

		// 재토글 — UNIQUE(scope_type, scope_key)로 행이 늘지 않고 값만 반전(재개).
		scope.toggleStock(ticker, actorId);
		assertThat(jdbc.queryForObject(
				"SELECT count(*) FROM serving_scope WHERE scope_type = 'INSTRUMENT' AND scope_key = ?",
				Integer.class, ticker)).isEqualTo(1);
		assertThat(currentStock(ticker).enabled()).isTrue();
	}

	@Test
	void 시장_토글은_MIC로_저장하고_기본은_옵트아웃_제공이다() {
		// 기본(행 부재) = 제공. 시장은 KRX 하나이고 serving_scope 엔 MIC(XKRX)로 저장된다.
		assertThat(market().enabled()).isTrue();

		scope.toggleMarket("KRX", actorId);
		assertThat(jdbc.queryForObject(
				"SELECT enabled FROM serving_scope WHERE scope_type = 'MARKET' AND scope_key = 'XKRX'",
				Boolean.class)).isFalse();
		assertThat(market().enabled()).isFalse();
	}

	private StockScope currentStock(String code) {
		return scope.listStocks().stream().filter(s -> s.code().equals(code)).findFirst().orElseThrow();
	}

	private MarketScope market() {
		return scope.listMarkets().get(0);
	}
}
