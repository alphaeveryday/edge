package com.edge.screening.repository;

import com.edge.screening.entity.Publication;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * publication 게시 원장 쓰기 — 이 모듈은 자동 게시·무효화 전이만 쓴다
 * (검수 승인 재발행·수동 제공 중단은 tenant-console-api — 스키마 COMMENT 의 writer 분담). grain 선점 INSERT·
 * 게시분 가드 UPDATE 는 save() 로 표현 불가라 native @Query 로 옮긴다 — 쓰기만 노출하려
 * Repository 마커를 상속한다.
 */
public interface PublicationRepository extends Repository<Publication, Long> {

	/**
	 * 자동 게시 — 다스냅샷 공존(ADR-0045 결정 3, ALPHA-743): 같은 (ticker, trade_date)의
	 * 다른 스냅샷(as_of)은 나란히 게시되고 표시가 as_of 최신을 고른다. 구 day-grain 선점
	 * 가드·교체(supersede, ALPHA-710) 규율은 은퇴했다. 남은 가드는 재수신 멱등(같은 item)
	 * 하나다. content_as_of 는 원장에서 직접 복사한다(ALPHA-918) — 호출부 배관을 늘리지
	 * 않고 승인 게시(tenant-console)와 같은 원천을 쓴다. FROM analysis_item 이 붙어 원장
	 * 행 부재 시 0행이 되는데, 게시는 항상 upsert 뒤라 그 경우는 원래 무결성 위반이다.
	 * @return 게시된 행 수(0 = 같은 item 재수신 skip).
	 */
	@Modifying
	@Transactional
	@Query(value = """
			INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, explanation_as_of, content_as_of)
			SELECT :analysisItemId, :etfTicker, :tradeDate, :explanationAsOf, ai.content_as_of
			FROM analysis_item ai
			WHERE ai.explanation_result_id = :analysisItemId
			AND NOT EXISTS (
			    SELECT 1 FROM publication WHERE analysis_item_id = :analysisItemId
			)
			""", nativeQuery = true)
	int publish(@Param("analysisItemId") String analysisItemId, @Param("etfTicker") String etfTicker,
			@Param("tradeDate") LocalDate tradeDate,
			@Param("explanationAsOf") OffsetDateTime explanationAsOf);

	/** 대상 item 의 게시분 상태 전이(INVALIDATED) — 즉시 비노출. @return 전이 행 수. */
	@Modifying
	@Transactional
	@Query(value = """
			UPDATE publication SET status = :status, unpublished_at = now()
			WHERE analysis_item_id = :analysisItemId AND status = 'PUBLISHED'
			""", nativeQuery = true)
	int transitionByItem(@Param("analysisItemId") String analysisItemId, @Param("status") String status);
}
