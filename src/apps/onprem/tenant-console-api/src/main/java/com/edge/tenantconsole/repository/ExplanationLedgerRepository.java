package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.model.FeedStatusAggregate;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.Collection;
import java.util.List;

/**
 * explanations 화면 원장 조회(ALPHA-607) — analysis_item read-only. 검수 큐 reader
 * (ReviewItemRepository)와 같은 @Immutable 엔티티를 읽되, 이 표면은 상태 전이를 쓰지
 * 않는 순수 조회다(쓰기는 아직 mock, ALPHA-497 후속). 검수 사유 파생은
 * screening_check→screening_rule 조인으로 별도 배치 조회한다(ReviewService 와 동형).
 */
public interface ExplanationLedgerRepository extends Repository<AnalysisItemEntity, String> {

	/** 화면 노출 상태(6종)의 설명을 최근 반입 순으로 — RECEIVED·CORRECTED·INVALIDATED 는 제외. */
	List<AnalysisItemEntity> findByStatusInOrderByReceivedAtDesc(Collection<String> statuses);

	/**
	 * 반입 흐름 집계 — 오늘(:since 이후) 반입 수와 최근 반입 시각을 한 스캔으로. 빈 원장은
	 * (0, NULL) 한 행으로 온다(집계 함수). "오늘"은 화면 노출 여부와 무관한 반입량이라
	 * 상태 필터 없이 전 analysis_item 을 센다.
	 */
	@Query("""
			SELECT new com.edge.tenantconsole.model.FeedStatusAggregate(
			    COALESCE(SUM(CASE WHEN a.receivedAt >= :since THEN 1 ELSE 0 END), 0),
			    MAX(a.receivedAt))
			FROM AnalysisItemEntity a
			""")
	FeedStatusAggregate summarizeFeed(@Param("since") OffsetDateTime since);
}
