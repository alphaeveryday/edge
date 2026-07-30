package com.edge.publication.service;

import com.edge.publication.dto.ExplanationResponse;
import com.edge.publication.entity.ServingScopeEntity;
import com.edge.publication.exposure.ExposureLogRecorder;
import com.edge.publication.repository.ExplanationStore;
import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.edge.publication.repository.ServingScopeRepository;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.Optional;

/**
 * 조회 오케스트레이션: 제공 범위 판정 → Published 조회 → 응답 조립 → Exposure 기록.
 * Published 외 상태는 이 서비스에 존재조차 하지 않는다 — 저장소가 Published 만 안다(제품 보장).
 */
@Service
public class ExplanationService {

	// 테넌트 정책의 기본 안내 문구 — 정책 테이블 도입 시 설정에서 읽는다.
	static final String DISCLAIMER = "본 내용은 공개 정보 기반의 변동 요인 후보이며 투자 권유가 아닙니다.";

	// 콘솔 "제공 범위" 토글의 scope_type·scope_key 어휘(serving_scope 스키마 COMMENT).
	// MARKET 저장 키는 MIC(XKRX, ADR-0027) — UI 표기 "KRX" 와 다르다. INSTRUMENT 키는 etf_ticker.
	private static final String SCOPE_MARKET = "MARKET";
	private static final String SCOPE_INSTRUMENT = "INSTRUMENT";
	private static final String MARKET_KRX = "XKRX";

	private final ExplanationStore store;
	private final ExposureLogRecorder exposureLogRecorder;
	private final ServingScopeRepository servingScopes;

	public ExplanationService(ExplanationStore store, ExposureLogRecorder exposureLogRecorder,
			ServingScopeRepository servingScopes) {
		this.store = store;
		this.exposureLogRecorder = exposureLogRecorder;
		this.servingScopes = servingScopes;
	}

	public boolean isKnownTicker(String ticker) {
		return store.isKnownTicker(ticker);
	}

	/** 200 대상이 있으면 응답을 만들고 그 시점에 Exposure 를 기록한다(조회=노출). */
	public Optional<ExplanationResponse> serve(String ticker, LocalDate tradeDate,
			String customerHash, String channel) {
		// 제공 범위 차단은 게시분 조회 앞단에서 걸러 "설명 없음"(204·Exposure 미기록)으로 수렴한다 —
		// 제외 사실을 고객 단에 드러내지 않고, 콘솔 토글이 캐시 없이 요청마다 즉시 반영된다(신선도 우선).
		if (isServingBlocked(ticker)) {
			return Optional.empty();
		}
		return store.findPublished(ticker, tradeDate).map(e -> {
			ExplanationResponse response = toResponse(e);
			exposureLogRecorder.record(e.publicationId(), e.ticker(), e.summary(), customerHash, channel);
			return response;
		});
	}

	/**
	 * 콘솔 제공 범위(옵트아웃) 판정 — 차단이면 게시분을 조회하지 않고 204 로 수렴시킨다.
	 * 규칙: ① MARKET(XKRX) OFF = 전역 차단(상위 우선 — 종목 토글 무시) ② INSTRUMENT(ticker)
	 * OFF = 종목 차단 ③ 행 부재·enabled=true = 제공. 조회는 요청당 PK 룩업 2회로 캐시 없음
	 * (Rule 2) — 콘솔 토글의 즉시 반영이 신선도 우선이라 게시분 캐시와 달리 캐시하지 않는다.
	 *
	 * <p>MARKET 토글이 전역 스위치인 것은 <b>KRX 단일 유니버스 전제(ADR-0024)</b> 때문이다 —
	 * 현행 서빙 데이터에 시장 식별 컬럼이 없어 종목별 시장 매핑이 불가하나, 유니버스가 XKRX
	 * 하나뿐이라 XKRX OFF = 전체 차단이 성립한다. 다중 시장 도입 시 시장 식별 공급과 함께 교체한다.
	 * CHANNEL·SECTOR 는 판정하지 않는다(no-op) — 콘솔에 해당 토글 writer UI 가 없어 행이 생길
	 * 경로 자체가 없다(SECTOR 는 섹터 식별 공급까지 보류, serving_scope 스키마 COMMENT).
	 */
	private boolean isServingBlocked(String ticker) {
		return isScopeDisabled(SCOPE_MARKET, MARKET_KRX) || isScopeDisabled(SCOPE_INSTRUMENT, ticker);
	}

	private boolean isScopeDisabled(String scopeType, String scopeKey) {
		return servingScopes.findByScopeTypeAndScopeKey(scopeType, scopeKey)
				.map(ServingScopeEntity::isEnabled)
				.map(enabled -> !enabled)
				.orElse(false);
	}

	private static ExplanationResponse toResponse(PublishedExplanation e) {
		return new ExplanationResponse(
				String.valueOf(e.publicationId()),
				new ExplanationResponse.EtfInfo(e.ticker(), e.etfName()),
				e.tradeDate(),
				e.summary(),
				e.confidenceLevel(),
				e.evidences().stream()
						.map(v -> new ExplanationResponse.EvidenceItem(v.kind(), v.title(), v.source(), v.publishedAt()))
						.toList(),
				DISCLAIMER,
				e.publishedAt()
		);
	}
}
