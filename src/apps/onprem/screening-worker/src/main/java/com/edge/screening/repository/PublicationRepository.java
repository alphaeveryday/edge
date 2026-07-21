package com.edge.screening.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;

/**
 * publication 게시 원장 쓰기 — 이 모듈은 자동 게시·무효화·정정 UNPUBLISHED 전이만 쓴다
 * (검수 승인 재발행은 tenant-console-api — 스키마 COMMENT 의 writer 분담).
 */
@Repository
public class PublicationRepository {

	private final JdbcTemplate jdbc;

	public PublicationRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	/**
	 * 자동 게시 — 게시 grain((ticker, trade_date) PUBLISHED 1건) 선점 시 skip.
	 * 재수신 멱등: 같은 item 의 게시가 이미 있으면 skip. @return 게시 여부.
	 */
	public boolean publish(String analysisItemId, String etfTicker, LocalDate tradeDate) {
		return jdbc.update("""
				INSERT INTO publication (analysis_item_id, etf_ticker, trade_date)
				SELECT ?, ?, ?
				WHERE NOT EXISTS (
				    SELECT 1 FROM publication WHERE analysis_item_id = ?
				)
				AND NOT EXISTS (
				    SELECT 1 FROM publication WHERE etf_ticker = ? AND trade_date = ? AND status = 'PUBLISHED'
				)
				""", analysisItemId, etfTicker, tradeDate, analysisItemId, etfTicker, tradeDate) > 0;
	}

	/** 대상 item 의 게시분 상태 전이(UNPUBLISHED·INVALIDATED) — 즉시 비노출. @return 전이 행 수. */
	public int transitionByItem(String analysisItemId, String status) {
		return jdbc.update("""
				UPDATE publication SET status = ?, unpublished_at = now()
				WHERE analysis_item_id = ? AND status = 'PUBLISHED'
				""", status, analysisItemId);
	}
}
