package com.edge.tenantconsole.model;

/**
 * 시장 제공 범위 — 시장 식별(UI id), 제공 여부, 그 시장에 속한 종목 수. MVP 커버리지는
 * 국내 상장 ETF 한정(ADR-0024)이라 시장은 KRX 하나이며, 시장 식별은 serving_scope 의
 * MIC(XKRX)로 저장되지만 UI 계약값은 "KRX" 다(ScopeService 가 매핑).
 */
public record MarketScope(String market, boolean enabled, int stockCount) {
}
