package com.edge.tenantconsole.controller;

import com.edge.tenantconsole.mock.ScopeMockStore.MarketScope;
import com.edge.tenantconsole.mock.ScopeMockStore.StockScope;
import com.edge.tenantconsole.service.ScopeService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 제공 범위 표면(ALPHA-513) — tenant-console-ui scope 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase.
 */
@RestController
public class ScopeController {

	private final ScopeService scopeService;

	public ScopeController(ScopeService scopeService) {
		this.scopeService = scopeService;
	}

	public record MarketScopeResponse(String market, boolean enabled, int stockCount) {
		static MarketScopeResponse from(MarketScope m) {
			return new MarketScopeResponse(m.market(), m.enabled(), m.stockCount());
		}
	}

	public record StockScopeResponse(String code, String name, String market, boolean enabled) {
		static StockScopeResponse from(StockScope s) {
			return new StockScopeResponse(s.code(), s.name(), s.market(), s.enabled());
		}
	}

	@GetMapping("/api/v1/scope/markets")
	public List<MarketScopeResponse> listMarkets() {
		return scopeService.listMarkets().stream().map(MarketScopeResponse::from).toList();
	}

	@PostMapping("/api/v1/scope/markets/{market}/toggle")
	public ResponseEntity<Void> toggleMarket(@PathVariable("market") String market) {
		scopeService.toggleMarket(market);
		return ResponseEntity.noContent().build();
	}

	@GetMapping("/api/v1/scope/stocks")
	public List<StockScopeResponse> listStocks() {
		return scopeService.listStocks().stream().map(StockScopeResponse::from).toList();
	}

	@PostMapping("/api/v1/scope/stocks/{code}/toggle")
	public ResponseEntity<Void> toggleStock(@PathVariable("code") String code) {
		scopeService.toggleStock(code);
		return ResponseEntity.noContent().build();
	}
}
