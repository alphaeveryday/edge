package com.edge.screening.repository;

import com.edge.screening.entity.Publication;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;

/**
 * publication 게시 원장 쓰기 — 이 모듈은 자동 게시·무효화 전이만 쓴다
 * (검수 승인 재발행·수동 제공 중단은 tenant-console-api — 스키마 COMMENT 의 writer 분담). grain 선점 INSERT·
 * 게시분 가드 UPDATE 는 save() 로 표현 불가라 native @Query 로 옮긴다 — 쓰기만 노출하려
 * Repository 마커를 상속한다.
 */
public interface PublicationRepository extends Repository<Publication, Long> {

	/**
	 * 자동 게시 — 게시 grain((ticker, trade_date) PUBLISHED 1건) 선점 시 skip.
	 * 재수신 멱등: 같은 item 의 게시가 이미 있으면 skip. @return 게시된 행 수(0 = 선점/중복 skip).
	 */
	@Modifying
	@Transactional
	@Query(value = """
			INSERT INTO publication (analysis_item_id, etf_ticker, trade_date)
			SELECT :analysisItemId, :etfTicker, :tradeDate
			WHERE NOT EXISTS (
			    SELECT 1 FROM publication WHERE analysis_item_id = :analysisItemId
			)
			AND NOT EXISTS (
			    SELECT 1 FROM publication WHERE etf_ticker = :etfTicker AND trade_date = :tradeDate
			                                    AND status = 'PUBLISHED'
			)
			""", nativeQuery = true)
	int publish(@Param("analysisItemId") String analysisItemId, @Param("etfTicker") String etfTicker,
			@Param("tradeDate") LocalDate tradeDate);

	/**
	 * 게시 grain 교체 대상 조회(ALPHA-710) — 같은 (ticker, trade_date)를 점유 중인
	 * **자동 게시본(AUTO_PUBLISHED)** 의 item id. 교체(구본 UNPUBLISHED 전이 후 재게시,
	 * uq_publication_published_grain 주석의 규율)는 자동 게시본끼리만이다 — 검수 승인본
	 * (APPROVED)을 자동이 끌어내리면 사람 결정이 기계에 덮인다(그 grain 은 선점 유지).
	 * 이 item 이 이미 게시 이력을 가지면 대상 없음 — 구본 재수신이 신본을 끌어내리고
	 * 자기는 게시(같은 item skip)도 못 해 grain 이 무게시로 남는 역전을 막는다.
	 */
	@Query(value = """
			SELECT p.analysis_item_id FROM publication p
			JOIN analysis_item ai ON ai.explanation_result_id = p.analysis_item_id
			WHERE p.etf_ticker = :etfTicker AND p.trade_date = :tradeDate
			  AND p.status = 'PUBLISHED' AND ai.status = 'AUTO_PUBLISHED'
			  AND p.analysis_item_id <> :analysisItemId
			  AND NOT EXISTS (
			      SELECT 1 FROM publication self WHERE self.analysis_item_id = :analysisItemId
			  )
			""", nativeQuery = true)
	String findSupersedableItem(@Param("analysisItemId") String analysisItemId,
			@Param("etfTicker") String etfTicker, @Param("tradeDate") LocalDate tradeDate);

	/** 대상 item 의 게시분 상태 전이(INVALIDATED) — 즉시 비노출. @return 전이 행 수. */
	@Modifying
	@Transactional
	@Query(value = """
			UPDATE publication SET status = :status, unpublished_at = now()
			WHERE analysis_item_id = :analysisItemId AND status = 'PUBLISHED'
			""", nativeQuery = true)
	int transitionByItem(@Param("analysisItemId") String analysisItemId, @Param("status") String status);
}
