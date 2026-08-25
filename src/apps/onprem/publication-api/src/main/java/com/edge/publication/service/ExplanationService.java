package com.edge.publication.service;

import com.edge.common.exception.GeneralException;
import com.edge.publication.dto.ExplanationResponse;
import com.edge.publication.entity.ServingScopeEntity;
import com.edge.publication.error.PublicationErrorStatus;
import com.edge.publication.repository.EtfInstrumentRepository;
import com.edge.publication.repository.ExplanationStore;
import com.edge.publication.repository.ExplanationStore.PublishedExplanation;
import com.edge.publication.repository.PolicyVersionRepository;
import com.edge.publication.repository.ServingScopeRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.Optional;

/**
 * 조회 오케스트레이션: 제공 범위 판정 → Published 조회 → 응답 조립(활성 정책의 면책 문구 동반).
 * Published 외 상태는 이 서비스에 존재조차 하지 않는다 — 저장소가 Published 만 안다(제품 보장).
 * 노출 이력(Exposure Log)은 기록하지 않는다 — 고객 단위 감사 요건과 함께 폐지됐다(ADR-0053).
 */
@Service
public class ExplanationService {

	private static final Logger log = LoggerFactory.getLogger(ExplanationService.class);

	/**
	 * 정책 발행 전(활성 버전 0건) 구간의 기본 안내 문구. 문자열은 콘솔
	 * {@code ScreeningService.DEFAULT_DISCLAIMER} 와 <b>같아야 한다</b> — 콘솔은 첫 발행 전
	 * 이 문구를 편집 화면에 투영하므로, 두 기본값이 갈리면 "콘솔에 보이는 문구"와 "고객에게
	 * 나가는 문구"가 아무도 아무것도 바꾸지 않은 상태에서 이미 어긋난다(ALPHA-772 의 발단).
	 * 문자열 하나 때문에 모듈 간 의존 간선을 늘리지 않고, scope 어휘(XKRX 등)와 같이 상호참조
	 * 주석으로 묶는다 — 활성 버전이 생기면 이 값은 더 이상 쓰이지 않는다.
	 */
	static final String DEFAULT_DISCLAIMER =
			"본 설명은 뉴스·공시 등 공개 데이터를 기반으로 자동 생성된 참고 정보이며, "
					+ "특정 종목의 매수·매도를 권유하지 않습니다. 투자 판단과 책임은 투자자 본인에게 있습니다.";

	// 콘솔 "제공 범위" 토글의 scope_type·scope_key 어휘(serving_scope 스키마 COMMENT).
	// MARKET 저장 키는 MIC(XKRX, ADR-0027) — UI 표기 "KRX" 와 다르다. INSTRUMENT 키는 etf_ticker.
	private static final String SCOPE_MARKET = "MARKET";
	private static final String SCOPE_INSTRUMENT = "INSTRUMENT";
	private static final String MARKET_KRX = "XKRX";

	private final ExplanationStore store;
	private final EtfInstrumentRepository etfInstrumentRepository;
	private final ServingScopeRepository servingScopeRepository;
	private final PolicyVersionRepository policyVersionRepository;

	public ExplanationService(ExplanationStore store, EtfInstrumentRepository etfInstrumentRepository,
	                          ServingScopeRepository servingScopeRepository,
							  PolicyVersionRepository policyVersionRepository) {
		this.store = store;
		this.etfInstrumentRepository = etfInstrumentRepository;
		this.servingScopeRepository = servingScopeRepository;
		this.policyVersionRepository = policyVersionRepository;
	}

	/** 상장 여부(404 판별) — 종목 마스터(etf_instrument, 증권사 환경 소유 데이터)로 판정한다. */
	public boolean isKnownTicker(String ticker) {
		return etfInstrumentRepository.existsByEtfTicker(ticker);
	}

	/** 200 대상이 있으면 응답을 만든다 — 없으면 empty(컨트롤러가 204 로 수렴). */
	public Optional<ExplanationResponse> serve(String ticker, LocalDate tradeDate) {

		if (!isKnownTicker(ticker)) {
			throw new GeneralException(PublicationErrorStatus.UNKNOWN_ETF);
		}

		// 제공 범위 차단은 게시분 조회 앞단에서 걸러 "설명 없음"으로 수렴한다 —
		// 제외 사실을 고객 단에 드러내지 않고, 콘솔 토글이 캐시 없이 요청마다 즉시 반영된다(신선도 우선).
		if (isServingBlocked(ticker)) {
			return Optional.empty();
		}

		return store.findPublished(ticker, tradeDate)
				.map(e -> toResponse(e, resolveDisclaimer()));
	}

	/**
	 * 응답에 실을 면책 문구를 활성 정책 버전에서 읽는다 — 콘솔 "면책 문구" 발행의 서빙단 실효화.
	 *
	 * <p><b>조회 시점 최신값</b>이다(게시 시점 스냅샷이 아니다). 면책 문구는 게시된 설명의 내용이
	 * 아니라 노출 화면에 동반되는 <b>현행 안내</b>라, 컴플라이언스가 문구를 고치면 이미 게시된
	 * 설명에도 즉시 적용되는 편이 통제 수단으로서 맞다. 같은 이유로 제공 범위 판정과 같이 캐시하지
	 * 않는다 — 게시분 캐시(ALPHA-433)는 read path 만 가리고 이 조회는 그 밖이라, 발행 즉시 반영된다.
	 * 과거 게시분에 당시 문구를 되살리는 소급 재현은 하지 않는다 — 노출 시점 재현 요건 자체가
	 * 폐지됐고(ADR-0053), 문구 변경 이력은 정책 버전 활성 구간으로만 남는다.
	 *
	 * <p>활성 버전 0건(첫 발행 전)은 정상 상태다 — 면책 문구는 테넌트 컴플라이언스 콘텐츠라
	 * 시드로 발행하지 않는다(policy_version 스키마 COMMENT). 그 구간은 콘솔이 편집 화면에 보여주는
	 * 것과 같은 기본 문구로 수렴시킨다.
	 */
	private String resolveDisclaimer() {
		Optional<String> active = policyVersionRepository.findActiveDisclaimerText();
		if (active.isPresent() && active.get().isBlank()) {
			// 콘솔은 공백 발행을 거부하므로(ScreeningService.updateDisclaimer 의 INVALID_REQUEST)
			// 여기 걸린다면 콘솔 밖 경로(마이그레이션·직접 SQL)가 만든 무결성 이상이다. 고객에겐
			// 안전한 기본 문구를 내보내되(빈 면책 문구 노출이 더 나쁘다) 그 사실을 조용히 삼키지
			// 않는다 — 폴백만 하면 콘솔의 활성 정책과 고객 노출이 갈려도 장애로 드러나지 않는다(Rule 12).
			log.error("활성 정책의 면책 문구가 비어 있다 — 기본 문구로 대체해 응답한다");
		}
		return active.filter(text -> !text.isBlank()).orElse(DEFAULT_DISCLAIMER);
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
		return servingScopeRepository.findByScopeTypeAndScopeKey(scopeType, scopeKey)
				.map(ServingScopeEntity::isEnabled)
				.map(enabled -> !enabled)
				.orElse(false);
	}

	private static ExplanationResponse toResponse(PublishedExplanation e, String disclaimer) {
		return new ExplanationResponse(
				String.valueOf(e.publicationId()),
				new ExplanationResponse.EtfInfo(e.ticker(), e.etfName()),
				e.tradeDate(),
				e.summary(),
				e.confidenceLevel(),
				e.evidences().stream()
						.map(v -> new ExplanationResponse.EvidenceItem(v.kind(), v.title(), v.source(), v.publishedAt()))
						.toList(),
				disclaimer,
				e.publishedAt(),
				e.explanationAsOf(),
				e.contentAsOf()
		);
	}
}
