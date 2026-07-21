package com.edge.publication.repository;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Published 설명 조회 — Publication API 의 유일한 데이터 소스.
 * 온프렘 Published Store(migrations-onprem: publication ⋈ analysis_item)를 조회하며,
 * WHERE 절이 Published(그리고 노출 가능 상태 AUTO_PUBLISHED·APPROVED)만 허용하므로
 * 그 외 상태는 이 층을 통과할 수 없다(제품 보장 — 계약 publication-api.md).
 */
@Component
public class ExplanationStore {

	/** 조회 도메인 모델 — publication_id 는 온프렘 발번(identity). */
	public record PublishedExplanation(
			long publicationId,
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

	private static final String SERVE_SQL = """
			SELECT p.publication_id, p.etf_ticker, a.etf_name, p.trade_date, a.summary,
			       a.confidence_level, a.evidences::text AS evidences, p.published_at
			FROM publication p
			JOIN analysis_item a ON a.explanation_result_id = p.analysis_item_id
			WHERE p.status = 'PUBLISHED'
			  AND a.status IN ('AUTO_PUBLISHED', 'APPROVED')
			  AND p.etf_ticker = ?
			""";

	private final JdbcTemplate jdbc;
	private final Set<String> knownTickers;
	private final ObjectMapper objectMapper = new ObjectMapper();

	public ExplanationStore(JdbcTemplate jdbc,
			@Value("${publication.known-tickers}") Set<String> knownTickers) {
		this.jdbc = jdbc;
		this.knownTickers = knownTickers;
	}

	/** 상장 여부(404 판별) — 종목 마스터 동기화 전의 설정 allowlist. */
	public boolean isKnownTicker(String ticker) {
		return knownTickers.contains(ticker);
	}

	/**
	 * 해당 ETF·일자의 Published 설명. trade_date 가 null 이면 **최신 거래일**의 게시분 —
	 * 화면(MTS AI 탭)은 "가장 최근 거래일의 분석"을 원하므로 게시 시각이 아니라
	 * 거래일을 우선 정렬한다(과거일 검수분이 늦게 게시돼도 최신 거래일이 이긴다).
	 */
	public Optional<PublishedExplanation> findPublished(String ticker, LocalDate tradeDate) {
		List<PublishedExplanation> rows = tradeDate == null
				? jdbc.query(SERVE_SQL + " ORDER BY p.trade_date DESC, p.published_at DESC LIMIT 1",
						rowMapper(), ticker)
				: jdbc.query(SERVE_SQL + " AND p.trade_date = ? ORDER BY p.published_at DESC LIMIT 1",
						rowMapper(), ticker, tradeDate);
		return rows.stream().findFirst();
	}

	private RowMapper<PublishedExplanation> rowMapper() {
		return (rs, rowNum) -> new PublishedExplanation(
				rs.getLong("publication_id"),
				rs.getString("etf_ticker"),
				rs.getString("etf_name"),
				rs.getObject("trade_date", LocalDate.class),
				rs.getString("summary"),
				rs.getString("confidence_level"),
				parseEvidences(rs.getString("evidences")),
				rs.getObject("published_at", OffsetDateTime.class));
	}

	/**
	 * analysis_item.evidences JSONB — [{kind, title, source, published_at}] (번들 경계면 형상).
	 * 형상 위반(배열 아님·비객체 요소)은 조용히 빈 근거로 만들지 않고 즉시 실패시킨다
	 * (Rule 12 fail-loud — 저장 데이터 오류를 200 응답이 은폐하면 안 된다).
	 */
	private List<PublishedExplanation.Evidence> parseEvidences(String json) {
		if (json == null || json.isBlank()) {
			return List.of();
		}
		JsonNode root = objectMapper.readTree(json);
		if (root.isNull()) {
			return List.of();
		}
		if (!root.isArray()) {
			throw new IllegalStateException("evidences JSONB 가 배열이 아니다: " + root.getNodeType());
		}
		List<PublishedExplanation.Evidence> evidences = new ArrayList<>();
		for (JsonNode node : root) {
			if (!node.isObject()) {
				throw new IllegalStateException("evidences 요소가 객체가 아니다: " + node.getNodeType());
			}
			evidences.add(new PublishedExplanation.Evidence(
					node.path("kind").asString(null),
					node.path("title").asString(null),
					node.path("source").asString(null),
					node.hasNonNull("published_at")
							? OffsetDateTime.parse(node.get("published_at").asString())
							: null));
		}
		return evidences;
	}
}
