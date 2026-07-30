package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.entity.ServingScopeEntity;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.MarketScope;
import com.edge.tenantconsole.model.StockScope;
import com.edge.tenantconsole.repository.ScopeInstrumentRepository;
import com.edge.tenantconsole.repository.ScopeInstrumentRepository.ScopeInstrumentRow;
import com.edge.tenantconsole.repository.ServingScopeRepository;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 제공 범위 표면(ALPHA-606, ALPHA-513 mock 대체) — 종목 유니버스는 analysis_item 실조회,
 * 제공 여부는 serving_scope 옵트아웃 토글이다(행 부재 = 기본 제공). MVP 커버리지는 국내
 * 상장 ETF 한정(ADR-0024)이라 시장은 KRX 하나이며, serving_scope 는 시장을 MIC(XKRX,
 * ADR-0027)로 저장하고 UI 계약값은 "KRX" 다. 온프렘 원장엔 시장 분류 컬럼이 없고
 * 소비자는 티커로 시장을 유추할 수 없어(ADR-0027) 종목의 시장은 KRX 상수로 채운다.
 */
@Service
public class ScopeService {

	private static final String SCOPE_MARKET = "MARKET";
	private static final String SCOPE_INSTRUMENT = "INSTRUMENT";

	/** UI 계약상 시장 식별값(경로·표시) ↔ serving_scope 저장 키(MIC, ADR-0027). */
	private static final String MARKET_KRX = "KRX";
	private static final String MARKET_KRX_MIC = "XKRX";

	private final ServingScopeRepository scopes;
	private final ScopeInstrumentRepository instruments;

	public ScopeService(ServingScopeRepository scopes, ScopeInstrumentRepository instruments) {
		this.scopes = scopes;
		this.instruments = instruments;
	}

	public List<MarketScope> listMarkets() {
		boolean enabled = scopes.findByScopeTypeAndScopeKey(SCOPE_MARKET, MARKET_KRX_MIC)
				.map(ServingScopeEntity::isEnabled)
				.orElse(true);   // 옵트아웃 — 행 부재 = 기본 제공
		int stockCount = instruments.findUniverse().size();
		return List.of(new MarketScope(MARKET_KRX, enabled, stockCount));
	}

	public void toggleMarket(String market, long actorMemberId) {
		if (!MARKET_KRX.equals(market)) {
			throw new GeneralException(ConsoleErrorStatus.SCOPE_TARGET_NOT_FOUND);
		}
		scopes.toggle(SCOPE_MARKET, MARKET_KRX_MIC, actorMemberId);
	}

	public List<StockScope> listStocks() {
		Map<String, Boolean> toggled = scopes.findByScopeType(SCOPE_INSTRUMENT).stream()
				.collect(Collectors.toMap(ServingScopeEntity::getScopeKey, ServingScopeEntity::isEnabled));
		return instruments.findUniverse().stream()
				.map(i -> new StockScope(i.getCode(), i.getName(), MARKET_KRX,
						toggled.getOrDefault(i.getCode(), true)))   // 옵트아웃 — 토글 이력 없으면 제공
				.toList();
	}

	public void toggleStock(String code, long actorMemberId) {
		if (!instruments.existsInUniverse(code)) {
			throw new GeneralException(ConsoleErrorStatus.SCOPE_TARGET_NOT_FOUND);
		}
		scopes.toggle(SCOPE_INSTRUMENT, code, actorMemberId);
	}
}
