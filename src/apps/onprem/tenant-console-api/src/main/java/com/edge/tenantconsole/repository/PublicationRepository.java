package com.edge.tenantconsole.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;

/**
 * publication 검수 승인 재발행 — writer 분담(스키마 COMMENT): 이 모듈은 검수 승인
 * 재발행만 쓴다(자동 게시·무효화는 screening-worker).
 */
@Repository
public class PublicationRepository {

	private final JdbcTemplate jdbc;

	public PublicationRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	/**
	 * 게시 grain((ticker, trade_date) PUBLISHED 1건) 선점 시 실패. 동시 승인 경합은
	 * 부분 유니크 인덱스를 arbiter 로 한 ON CONFLICT 가 원자적으로 한쪽만 통과시킨다
	 * (NOT EXISTS 선검사는 경합에서 unique 위반 500 이 된다 — 리뷰 지적).
	 * @return 게시 여부.
	 */
	public boolean publish(String analysisItemId, String etfTicker, LocalDate tradeDate) {
		return jdbc.update("""
				INSERT INTO publication (analysis_item_id, etf_ticker, trade_date)
				VALUES (?, ?, ?)
				ON CONFLICT (etf_ticker, trade_date) WHERE status = 'PUBLISHED' DO NOTHING
				""", analysisItemId, etfTicker, tradeDate) > 0;
	}
}
