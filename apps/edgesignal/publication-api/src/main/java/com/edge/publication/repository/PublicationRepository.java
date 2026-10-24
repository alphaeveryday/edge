package com.edge.publication.repository;

import com.edge.publication.entity.Publication;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.Optional;

/**
 * Published 설명 조회 — publication ⋈ analysis_item. WHERE 절이 Published(그리고 노출 가능 상태
 * AUTO_PUBLISHED·APPROVED)만 허용하므로 그 외 상태는 이 층을 통과할 수 없다(계약 publication-api.md).
 * 읽기 전용 — save/delete 를 노출하지 않으려 JpaRepository 가 아니라 Repository 마커를 상속한다.
 * analysisItem 은 @ManyToOne(to-one) 이라 join fetch + LIMIT 이 인메모리 페이징 없이 SQL 로 내려간다.
 */
public interface PublicationRepository extends Repository<Publication, Long> {

	String SERVE_JPQL = """
			SELECT p FROM Publication p
			JOIN FETCH p.analysisItem a
			WHERE p.status = 'PUBLISHED'
			  AND a.status IN ('AUTO_PUBLISHED', 'APPROVED')
			  AND p.etfTicker = :ticker
			""";

	/**
	 * 해당 ETF 의 최신 거래일 게시분. 화면(MTS AI 탭)은 "가장 최근 거래일의 분석"을 원하므로 게시
	 * 시각이 아니라 거래일을 우선 정렬한다(과거일 검수분이 늦게 게시돼도 최신 거래일이 이긴다).
	 * 같은 거래일의 유효 스냅샷이 여럿이면 explanation_as_of 최신이 이긴다 — "유효 최신 승리"
	 * (ADR-0045 결정 3, ALPHA-743). 최신이 무효화(WHERE 게이트 탈락)되면 직전 스냅샷이 이
	 * 정렬만으로 자동 노출된다(별도 fallback 로직 없음). publishedAt 은 동률 해소.
	 */
	@Query(SERVE_JPQL + " ORDER BY p.tradeDate DESC, p.explanationAsOf DESC, p.publishedAt DESC")
	Optional<Publication> findLatestPublished(@Param("ticker") String ticker, Limit limit);

	/** 특정 거래일의 게시분 — 유효 스냅샷 중 explanation_as_of 최신이 이긴다(같은 규칙). */
	@Query(SERVE_JPQL + " AND p.tradeDate = :tradeDate"
			+ " ORDER BY p.explanationAsOf DESC, p.publishedAt DESC")
	Optional<Publication> findPublishedOn(@Param("ticker") String ticker,
			@Param("tradeDate") LocalDate tradeDate, Limit limit);
}
