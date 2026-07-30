package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.MarketScope;

/**
 * 시장 제공 범위 응답(ALPHA-513) — tenant-console-ui scope 타입과 1:1 camelCase.
 * 도메인 model(MarketScope)과 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record MarketScopeResponse(String market, boolean enabled, int stockCount) {

	public static MarketScopeResponse from(MarketScope m) {
		return new MarketScopeResponse(m.market(), m.enabled(), m.stockCount());
	}
}
