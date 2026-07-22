package com.edge.tenantconsole.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/**
 * analysis_item 검수 조회·검수 결정 전이 — writer 분담(스키마 COMMENT): 이 모듈은
 * 검수 결정 전이(REVIEW_REQUIRED → APPROVED | REJECTED)만 쓴다(수신·자동 분기는 screening-worker).
 * Review Queue 는 물리 큐가 아니라 status 기반 논리 작업함이다(console-ia).
 */
@Repository
public class ReviewItemRepository {

	public record ReviewItem(
			String explanationResultId,
			String etfTicker,
			String etfName,
			LocalDate tradeDate,
			String summary,
			String headline,
			String confidenceLevel,
			String status,
			String supersedesItemId,
			String correctionReason,
			OffsetDateTime receivedAt
	) {
	}

	private static final String SELECT_SQL = """
			SELECT explanation_result_id, etf_ticker, etf_name, trade_date, summary, headline,
			       confidence_level, status, supersedes_item_id, correction_reason, received_at
			FROM analysis_item
			""";

	private final JdbcTemplate jdbc;

	public ReviewItemRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	public List<ReviewItem> findByStatus(String status, int limit) {
		return jdbc.query(SELECT_SQL + " WHERE status = ? ORDER BY received_at LIMIT ?",
				rowMapper(), status, limit);
	}

	public Optional<ReviewItem> findById(String explanationResultId) {
		return jdbc.query(SELECT_SQL + " WHERE explanation_result_id = ?", rowMapper(), explanationResultId)
				.stream().findFirst();
	}

	/**
	 * 검수 결정 전이 — REVIEW_REQUIRED 에서만 허용(동시 결정·재결정은 0행 = 충돌).
	 * @return 전이 성공 여부.
	 */
	public boolean decide(String explanationResultId, String decidedStatus) {
		return jdbc.update("""
				UPDATE analysis_item SET status = ?, updated_at = now()
				WHERE explanation_result_id = ? AND status = 'REVIEW_REQUIRED'
				""", decidedStatus, explanationResultId) > 0;
	}

	private RowMapper<ReviewItem> rowMapper() {
		return (rs, n) -> new ReviewItem(
				rs.getString("explanation_result_id"),
				rs.getString("etf_ticker"),
				rs.getString("etf_name"),
				rs.getObject("trade_date", LocalDate.class),
				rs.getString("summary"),
				rs.getString("headline"),
				rs.getString("confidence_level"),
				rs.getString("status"),
				rs.getString("supersedes_item_id"),
				rs.getString("correction_reason"),
				rs.getObject("received_at", OffsetDateTime.class));
	}
}
