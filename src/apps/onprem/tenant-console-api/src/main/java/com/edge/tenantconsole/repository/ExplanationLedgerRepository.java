package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.model.FeedStatusAggregate;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

/**
 * explanations 화면 원장 조회 + 사후 운영 전이(ALPHA-607·613) — analysis_item.
 * 검수 큐 reader(ReviewItemRepository)와 같은 @Immutable 엔티티를 읽고, 사후 운영
 * 전이(제공 중단·검수 이관)만 쓴다(검수 결정 전이는 ReviewItemRepository 소관). 그
 * 전이는 읽은 상태를 WHERE 가드로 박은 native @Modifying 이라 @Immutable 엔티티의
 * dirty-write 로 표현할 수 없다(경합·재전이 0행 = 충돌). 검수 사유 파생은
 * screening_check→screening_rule 조인으로 별도 배치 조회한다(ReviewService 와 동형).
 */
public interface ExplanationLedgerRepository extends Repository<AnalysisItemEntity, String> {

	/** 화면 노출 상태(6종)의 설명을 최근 반입 순으로 — RECEIVED·CORRECTED·INVALIDATED 는 제외. */
	List<AnalysisItemEntity> findByStatusInOrderByReceivedAtDesc(Collection<String> statuses);

	/** 사후 운영 전이의 not-found(404) 분기용 — 현재 상태를 읽어 가드 값으로 쓴다. */
	Optional<AnalysisItemEntity> findById(String explanationResultId);

	/**
	 * 제공 중단 — 노출 중(:expectedStatus, AUTO_PUBLISHED|APPROVED)에서만 UNPUBLISHED 로.
	 * 읽은 상태를 WHERE 에 박아 경합·재중단은 0행(충돌)이 된다(decide 동형).
	 * @return 전이된 행 수(1 = 성공, 0 = 충돌).
	 */
	@Transactional
	@Modifying
	@Query(value = """
			UPDATE analysis_item SET status = 'UNPUBLISHED', updated_at = now()
			WHERE explanation_result_id = :id AND status = :expectedStatus
			""", nativeQuery = true)
	int unpublish(@Param("id") String explanationResultId,
			@Param("expectedStatus") String expectedStatus);

	/**
	 * 검수 이관 — 점검 차단(BLOCKED)에서만 REVIEW_REQUIRED 로. 차단이 아닌 건의 이관은
	 * 0행(충돌)이 된다.
	 * @return 전이된 행 수(1 = 성공, 0 = 충돌).
	 */
	@Transactional
	@Modifying
	@Query(value = """
			UPDATE analysis_item SET status = 'REVIEW_REQUIRED', updated_at = now()
			WHERE explanation_result_id = :id AND status = 'BLOCKED'
			""", nativeQuery = true)
	int moveBlockedToReview(@Param("id") String explanationResultId);

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
