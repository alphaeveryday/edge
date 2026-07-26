package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.PublicationEntity;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;

/**
 * publication 검수 승인 재발행 — writer 분담(스키마 COMMENT): 이 모듈은 검수 승인
 * 재발행만 쓴다(자동 게시·무효화는 screening-worker). 게시 grain((ticker,trade_date)
 * PUBLISHED 1건) 경합은 부분 유니크 인덱스를 arbiter 로 한 native ON CONFLICT 가
 * 원자적으로 한쪽만 통과시킨다 — JPA persist 로는 표현 불가.
 */
public interface PublicationRepository extends Repository<PublicationEntity, Long> {

	/**
	 * 게시 시점 노출 문구를 published_summary 로 스냅샷한다(ALPHA-437) — 수정 승인은
	 * 편집 문구, 일반 승인은 원문(analysis_item.summary)이 실린다(DDL 주석의 필수화).
	 *
	 * @return 삽입된 행 수(1 = 게시, 0 = grain 선점).
	 */
	@Transactional
	@Modifying
	@Query(value = """
			INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, published_summary)
			VALUES (:analysisItemId, :etfTicker, :tradeDate, :publishedSummary)
			ON CONFLICT (etf_ticker, trade_date) WHERE status = 'PUBLISHED' DO NOTHING
			""", nativeQuery = true)
	int publish(@Param("analysisItemId") String analysisItemId, @Param("etfTicker") String etfTicker,
			@Param("tradeDate") LocalDate tradeDate,
			@Param("publishedSummary") String publishedSummary);
}
