package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.StockScope;

/**
 * 종목 제공 범위 응답(ALPHA-513) — tenant-console-ui scope 타입과 1:1 camelCase.
 * 도메인 model(StockScope)과 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record StockScopeResponse(String code, String name, String market, boolean enabled) {

	public static StockScopeResponse from(StockScope s) {
		return new StockScopeResponse(s.code(), s.name(), s.market(), s.enabled());
	}
}
