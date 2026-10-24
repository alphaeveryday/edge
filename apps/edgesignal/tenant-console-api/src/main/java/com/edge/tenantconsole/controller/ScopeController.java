package com.edge.tenantconsole.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.MarketScopeResponse;
import com.edge.tenantconsole.dto.StockScopeResponse;
import com.edge.tenantconsole.service.ScopeService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 제공 범위 표면(ALPHA-513 mock → ALPHA-606 실 DB) — tenant-console-ui scope 도메인
 * 계약과 1:1. 필드명은 UI 타입과 동일한 camelCase. 토글은 제공 범위 변경 감사 주체가
 * 필요해 세션 actor 의 member_id 를 서비스로 전달한다(ConsoleAuthFilter 가 non-null 보장).
 */
@RestController
public class ScopeController {

	private final ScopeService scopeService;

	public ScopeController(ScopeService scopeService) {
		this.scopeService = scopeService;
	}

	@GetMapping("/api/v1/scope/markets")
	public ApiResponse<List<MarketScopeResponse>> listMarkets() {
		return ApiResponse.onSuccess(
				scopeService.listMarkets().stream().map(MarketScopeResponse::from).toList());
	}

	@PostMapping("/api/v1/scope/markets/{market}/toggle")
	public ApiResponse<Void> toggleMarket(@PathVariable("market") String market,
			HttpServletRequest httpRequest) {
		scopeService.toggleMarket(market, actor(httpRequest).memberId());
		return ApiResponse.onSuccess(null);
	}

	@GetMapping("/api/v1/scope/stocks")
	public ApiResponse<List<StockScopeResponse>> listStocks() {
		return ApiResponse.onSuccess(
				scopeService.listStocks().stream().map(StockScopeResponse::from).toList());
	}

	@PostMapping("/api/v1/scope/stocks/{code}/toggle")
	public ApiResponse<Void> toggleStock(@PathVariable("code") String code,
			HttpServletRequest httpRequest) {
		scopeService.toggleStock(code, actor(httpRequest).memberId());
		return ApiResponse.onSuccess(null);
	}

	private static SessionMember actor(HttpServletRequest request) {
		return (SessionMember) request.getSession(false).getAttribute(SessionMember.SESSION_KEY);
	}
}
