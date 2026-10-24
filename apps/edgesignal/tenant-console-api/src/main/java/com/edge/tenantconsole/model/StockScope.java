package com.edge.tenantconsole.model;

/**
 * 종목 제공 범위 — 종목 코드(서빙 키 etf_ticker)·이름, 소속 시장, 제공 여부. 종목
 * 유니버스는 analysis_item(실제 수신된 ETF)에서 조회하고 제공 여부는 serving_scope
 * INSTRUMENT 토글로 덧씌운다(행 부재 = 기본 제공). 온프렘 원장엔 시장 분류 컬럼이
 * 없고 소비자는 티커로 시장을 유추할 수 없어(ADR-0027) 시장은 KRX 상수다(ADR-0024 MVP).
 */
public record StockScope(String code, String name, String market, boolean enabled) {
}
