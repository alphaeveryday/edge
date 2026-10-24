package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.PublicationEntity;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * publication writer — writer 분담(스키마 COMMENT): 이 모듈은 검수 승인 재발행과
 * 사후 운영(최종 문구 정정·수동 제공 중단)만 쓴다(자동 게시·무효화는 screening-worker).
 * 게시 grain 은 (ticker, trade_date, explanation_as_of) — 다스냅샷 공존(ADR-0045 결정 3,
 * ALPHA-743)이라 같은 스냅샷의 이중 게시만 부분 유니크 인덱스 arbiter 의 native
 * ON CONFLICT 가 원자적으로 막는다 — JPA persist 로는 표현 불가.
 */
public interface PublicationRepository extends Repository<PublicationEntity, Long> {

	/**
	 * 게시 시점 노출 문구를 published_summary 로 스냅샷한다(ALPHA-437) — 수정 승인은
	 * 편집 문구, 일반 승인은 원문(analysis_item.summary)이 실린다(DDL 주석의 필수화).
	 *
	 * content_as_of 는 원장에서 직접 복사한다(ALPHA-918) — 자동 게시(screening-worker)와
	 * 같은 원천. FROM analysis_item 이 붙어 원장 행 부재 시 0행인데, 승인 게시는 항상
	 * 원장 행을 읽은 뒤라 그 경우는 원래 무결성 위반이다.
	 *
	 * @return 삽입된 행 수(1 = 게시, 0 = 같은 스냅샷 이중 게시).
	 */
	@Transactional
	@Modifying
	@Query(value = """
			INSERT INTO publication (analysis_item_id, etf_ticker, trade_date, explanation_as_of,
			                         published_summary, content_as_of)
			SELECT :analysisItemId, :etfTicker, :tradeDate, :explanationAsOf, :publishedSummary,
			       ai.content_as_of
			FROM analysis_item ai
			WHERE ai.explanation_result_id = :analysisItemId
			ON CONFLICT (etf_ticker, trade_date, explanation_as_of) WHERE status = 'PUBLISHED'
			DO NOTHING
			""", nativeQuery = true)
	int publish(@Param("analysisItemId") String analysisItemId, @Param("etfTicker") String etfTicker,
			@Param("tradeDate") LocalDate tradeDate,
			@Param("explanationAsOf") OffsetDateTime explanationAsOf,
			@Param("publishedSummary") String publishedSummary);

	/**
	 * 최종 제공 문구 정정(ALPHA-613) — 게시본(PUBLISHED)의 노출 문구를 in-place 교체한다.
	 * final 은 화면이 publication 에서 읽으므로(읽기 정합) 여기에 써야 반영된다
	 * (analysis_item.summary 는 원문 보존 규약이라 손대지 않는다). 게시본 없는 항목은
	 * 0행(NOT_PUBLISHED)이 된다.
	 *
	 * @return 갱신된 행 수(1 = 정정, 0 = 게시본 없음).
	 */
	@Transactional
	@Modifying
	@Query(value = """
			UPDATE publication SET published_summary = :publishedSummary
			WHERE analysis_item_id = :analysisItemId AND status = 'PUBLISHED'
			""", nativeQuery = true)
	int updatePublishedSummary(@Param("analysisItemId") String analysisItemId,
			@Param("publishedSummary") String publishedSummary);

	/**
	 * 수동 제공 중단(ALPHA-613) — 게시본(PUBLISHED)을 UNPUBLISHED 로 내리고 감사 메타
	 * (사유·실행자·중단 시각)를 함께 남긴다. 사유·실행자는 스키마 제약상 짝으로만 존재하고
	 * (ck_publication_unpublish_meta_pair), UNPUBLISHED 행에만 허용된다. 게시본 없는
	 * 항목은 0행(NOT_PUBLISHED)이 된다.
	 *
	 * @return 갱신된 행 수(1 = 중단, 0 = 게시본 없음).
	 */
	@Transactional
	@Modifying
	@Query(value = """
			UPDATE publication
			SET status = 'UNPUBLISHED', unpublish_reason = :reason, unpublished_by = :memberId,
			    unpublished_at = now()
			WHERE analysis_item_id = :analysisItemId AND status = 'PUBLISHED'
			""", nativeQuery = true)
	int unpublish(@Param("analysisItemId") String analysisItemId, @Param("reason") String reason,
			@Param("memberId") Long memberId);
}
