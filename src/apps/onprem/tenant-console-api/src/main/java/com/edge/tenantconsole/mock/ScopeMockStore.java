package com.edge.tenantconsole.mock;

import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * scope 도메인 mock 데이터 — 시장·종목 단위 제공 여부. UI 시안(v0.2) 종목 유니버스
 * 이식 in-memory 가변 스토어. DB 연동 시 service 의 이 스토어 호출부를 repository 로
 * 교체한다(ALPHA-513).
 */
@Component
public class ScopeMockStore {

	public record MarketScope(String market, boolean enabled, int stockCount) {
	}

	public record StockScope(String code, String name, String market, boolean enabled) {
	}

	private final Map<String, Boolean> markets = new LinkedHashMap<>();
	private final List<StockScope> stocks = new ArrayList<>(List.of(
			new StockScope("005930", "삼성전자", "KRX", true),
			new StockScope("247540", "에코프로비엠", "KRX", true),
			new StockScope("000660", "SK하이닉스", "KRX", true),
			new StockScope("NVDA", "NVIDIA", "NASDAQ", true),
			new StockScope("035720", "카카오", "KRX", true),
			new StockScope("TSLA", "Tesla", "NASDAQ", true),
			new StockScope("068270", "셀트리온", "KRX", true),
			new StockScope("AAPL", "Apple", "NASDAQ", true),
			new StockScope("003670", "포스코퓨처엠", "KRX", true),
			new StockScope("034020", "두산에너빌리티", "KRX", true),
			new StockScope("AMD", "AMD", "NASDAQ", true),
			new StockScope("RIVN", "Rivian", "NASDAQ", true),
			new StockScope("373220", "LG에너지솔루션", "KRX", true)));

	public ScopeMockStore() {
		markets.put("KRX", true);
		markets.put("NASDAQ", true);
	}

	public synchronized List<MarketScope> listMarkets() {
		return markets.entrySet().stream()
				.map(e -> new MarketScope(e.getKey(), e.getValue(),
						(int) stocks.stream().filter(s -> s.market().equals(e.getKey())).count()))
				.toList();
	}

	public synchronized boolean toggleMarket(String market) {
		Boolean enabled = markets.get(market);
		if (enabled == null) {
			return false;
		}
		markets.put(market, !enabled);
		return true;
	}

	public synchronized List<StockScope> listStocks() {
		return List.copyOf(stocks);
	}

	public synchronized boolean toggleStock(String code) {
		for (int i = 0; i < stocks.size(); i++) {
			StockScope s = stocks.get(i);
			if (s.code().equals(code)) {
				stocks.set(i, new StockScope(s.code(), s.name(), s.market(), !s.enabled()));
				return true;
			}
		}
		return false;
	}
}
