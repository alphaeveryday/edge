package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.PublicationEntity;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

/**
 * publication 게시 문구 조회(ALPHA-607) — explanations 최종 문구(final)를 게시 원장에서
 * 읽는다. 검수 승인 재발행·사후 운영(PublicationRepository)이 writer 이고 이쪽은
 * reader 다(같은 엔티티, 관심사 분리). 목록 최종 문구는 항목당 개별 조회(N+1)를 피해
 * 배치로 읽는다.
 */
public interface PublishedSummaryRepository extends Repository<PublicationEntity, Long> {

	/**
	 * 항목들의 게시 이력 전부 — 상태 무관. 제공 중단(UNPUBLISHED)된 항목도 마지막 게시
	 * 문구(published_summary)를 final 로 보존해야 하므로 PUBLISHED 로 좁히지 않고, 서비스가
	 * 항목별 최신 게시본(publication_id 최대)을 고른다.
	 */
	List<PublicationEntity> findByAnalysisItemIdIn(Collection<String> analysisItemIds);

	/**
	 * 항목의 게시본(:status) 1건 — 최종 문구 정정(ALPHA-613)의 전값 스냅샷용. 게시 grain 상
	 * PUBLISHED 는 항목당 최대 1건이나, 방어적으로 최신본(publication_id 최대)을 취한다.
	 */
	Optional<PublicationEntity> findFirstByAnalysisItemIdAndStatusOrderByPublicationIdDesc(
			String analysisItemId, String status);

	/**
	 * 티커별 현재 노출 head 의 analysis_item_id(ALPHA-744) — publication-api 서빙 술어
	 * (PublicationRepository SERVE_JPQL + findLatestPublished 정렬 + ExplanationService
	 * isServingBlocked 의 제공 범위 게이트)의 전사이며 그쪽이 SSOT 다: 제공 범위 미차단
	 * (MARKET XKRX·INSTRUMENT 옵트아웃 OFF 아님 — 행 부재=제공) × 게시본 PUBLISHED ×
	 * 항목 AUTO_PUBLISHED|APPROVED 중 trade_date → explanation_as_of → published_at 최신
	 * 1건. as_of 만으로 파생하면 무효화 fallback(직전 스냅샷 재노출)을 놓친다 — 서빙
	 * 정렬·상태 필터·범위 규칙이 바뀌면 여기도 함께 고친다.
	 */
	@Query(value = """
			SELECT DISTINCT ON (p.etf_ticker) p.analysis_item_id
			  FROM publication p
			  JOIN analysis_item a ON a.explanation_result_id = p.analysis_item_id
			 WHERE p.status = 'PUBLISHED' AND a.status IN ('AUTO_PUBLISHED', 'APPROVED')
			   AND NOT EXISTS (SELECT 1 FROM serving_scope s
			                    WHERE s.enabled = false
			                      AND ((s.scope_type = 'MARKET' AND s.scope_key = 'XKRX')
			                        OR (s.scope_type = 'INSTRUMENT' AND s.scope_key = p.etf_ticker)))
			 ORDER BY p.etf_ticker, p.trade_date DESC, p.explanation_as_of DESC, p.published_at DESC
			""", nativeQuery = true)
	List<String> findServingHeadItemIds();
}
