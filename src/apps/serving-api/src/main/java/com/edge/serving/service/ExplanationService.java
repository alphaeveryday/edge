package com.edge.serving.service;

import com.edge.serving.dto.ExplanationResponse;
import com.edge.serving.exposure.ExposureLogRecorder;
import com.edge.serving.repository.ExplanationStore;
import com.edge.serving.repository.ExplanationStore.PublishedExplanation;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.Optional;

/**
 * 조회 오케스트레이션: Published 조회 → 응답 조립 → Exposure 기록.
 * Published 외 상태는 이 서비스에 존재조차 하지 않는다 — 저장소가 Published 만 안다(제품 보장).
 */
@Service
public class ExplanationService {

	// 테넌트 정책의 기본 안내 문구 — 정책 테이블 도입 시 설정에서 읽는다.
	static final String DISCLAIMER = "본 내용은 공개 정보 기반의 변동 요인 후보이며 투자 권유가 아닙니다.";

	private final ExplanationStore store;
	private final ExposureLogRecorder exposureLogRecorder;

	public ExplanationService(ExplanationStore store, ExposureLogRecorder exposureLogRecorder) {
		this.store = store;
		this.exposureLogRecorder = exposureLogRecorder;
	}

	public boolean isKnownTicker(String ticker) {
		return store.isKnownTicker(ticker);
	}

	/** 200 대상이 있으면 응답을 만들고 그 시점에 Exposure 를 기록한다(조회=노출). */
	public Optional<ExplanationResponse> serve(String ticker, LocalDate tradeDate,
			String customerHash, String channel) {
		return store.findPublished(ticker, tradeDate).map(e -> {
			ExplanationResponse response = toResponse(e);
			exposureLogRecorder.record(e.publicationId(), e.ticker(), e.summary(), customerHash, channel);
			return response;
		});
	}

	private static ExplanationResponse toResponse(PublishedExplanation e) {
		return new ExplanationResponse(
				e.publicationId(),
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
