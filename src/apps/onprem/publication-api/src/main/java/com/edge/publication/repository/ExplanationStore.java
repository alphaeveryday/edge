package com.edge.publication.repository;

import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Published 설명 조회 — Publication API 의 유일한 데이터 소스.
 * 현재는 데모·테스트용 인메모리 시드. 온프렘 도메인 마이그레이션(state-machine ERD)
 * 확정 시 이 클래스를 DB 조회(Published 상태 필터)로 직접 재작성한다.
 * 시드는 tenant-sync-api 시드(069500, 2026-07-15)와 정합 — 데모 서사가 이어진다.
 */
@Component
public class ExplanationStore {

	/** 조회 대상 도메인 모델(인메모리) — 영속화 시 온프렘 publications 테이블로 대체. */
	public record PublishedExplanation(
			String publicationId,
			String ticker,
			String etfName,
			LocalDate tradeDate,
			String summary,
			String confidenceLevel,
			List<Evidence> evidences,
			OffsetDateTime publishedAt
	) {
		public record Evidence(String kind, String title, String source, OffsetDateTime publishedAt) {
		}
	}

	private static final LocalDate TRADE_DATE = LocalDate.of(2026, 7, 15);
	private static final OffsetDateTime PUBLISHED_AT =
			OffsetDateTime.of(2026, 7, 15, 16, 40, 0, 0, ZoneOffset.ofHours(9));

	// 상장 여부 판별(404)과 설명 존재 여부(204)는 다른 질문이다 — 305720 은 상장이지만 설명 없음.
	private static final Set<String> KNOWN_TICKERS = Set.of("069500", "305720");

	private final Map<String, PublishedExplanation> published = Map.of(
			"069500", new PublishedExplanation(
					"pub-20260715-069500-0001", "069500", "KODEX 200", TRADE_DATE,
					"반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.",
					"MEDIUM",
					List.of(new PublishedExplanation.Evidence(
							"NEWS", "반도체 수출 반등", "demo",
							OffsetDateTime.of(2026, 7, 15, 13, 0, 0, 0, ZoneOffset.ofHours(9)))),
					PUBLISHED_AT)
	);

	public boolean isKnownTicker(String ticker) {
		return KNOWN_TICKERS.contains(ticker);
	}

	/** 해당 ETF·일자의 Published 설명. trade_date 가 null 이면 가장 최근 게시분. */
	public Optional<PublishedExplanation> findPublished(String ticker, LocalDate tradeDate) {
		PublishedExplanation e = published.get(ticker);
		if (e == null) {
			return Optional.empty();
		}
		if (tradeDate != null && !e.tradeDate().equals(tradeDate)) {
			return Optional.empty();
		}
		return Optional.of(e);
	}
}
