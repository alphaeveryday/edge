package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.mock.ScopeMockStore;
import com.edge.tenantconsole.mock.ScopeMockStore.MarketScope;
import com.edge.tenantconsole.mock.ScopeMockStore.StockScope;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 제공 범위 mock 표면(ALPHA-513) — not-found 판정만 하고 mock 스토어에 위임한다.
 * DB 연동 시 스토어 의존을 repository 로 교체한다.
 */
@Service
public class ScopeService {

	private final ScopeMockStore store;

	public ScopeService(ScopeMockStore store) {
		this.store = store;
	}

	public List<MarketScope> listMarkets() {
		return store.listMarkets();
	}

	public void toggleMarket(String market) {
		if (!store.toggleMarket(market)) {
			throw new GeneralException(ConsoleErrorStatus.SCOPE_TARGET_NOT_FOUND);
		}
	}

	public List<StockScope> listStocks() {
		return store.listStocks();
	}

	public void toggleStock(String code) {
		if (!store.toggleStock(code)) {
			throw new GeneralException(ConsoleErrorStatus.SCOPE_TARGET_NOT_FOUND);
		}
	}
}
