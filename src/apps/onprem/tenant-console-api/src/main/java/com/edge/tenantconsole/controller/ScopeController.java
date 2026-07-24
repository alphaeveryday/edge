package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.mock.ScopeMockStore.MarketScope;
import com.edge.tenantconsole.mock.ScopeMockStore.StockScope;
import com.edge.tenantconsole.service.ScopeService;
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
	public ApiResponse<List<MarketScopeResponse>> listMarkets() {
		return ApiResponse.onSuccess(
				scopeService.listMarkets().stream().map(MarketScopeResponse::from).toList());
	}

	@PostMapping("/api/v1/scope/markets/{market}/toggle")
	public ApiResponse<Void> toggleMarket(@PathVariable("market") String market) {
		scopeService.toggleMarket(market);
		return ApiResponse.onSuccess(null);
	}

	@GetMapping("/api/v1/scope/stocks")
	public ApiResponse<List<StockScopeResponse>> listStocks() {
		return ApiResponse.onSuccess(
				scopeService.listStocks().stream().map(StockScopeResponse::from).toList());
	}

	@PostMapping("/api/v1/scope/stocks/{code}/toggle")
	public ApiResponse<Void> toggleStock(@PathVariable("code") String code) {
		scopeService.toggleStock(code);
		return ApiResponse.onSuccess(null);
	}
}
