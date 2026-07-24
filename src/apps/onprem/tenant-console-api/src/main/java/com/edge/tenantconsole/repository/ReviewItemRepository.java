package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.AnalysisItemEntity;
import org.springframework.data.domain.Limit;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;

/**
 * analysis_item 검수 조회·검수 결정 전이 — writer 분담(스키마 COMMENT): 이 모듈은
 * 검수 결정 전이(REVIEW_REQUIRED → APPROVED | REJECTED)만 쓴다(수신·자동 분기는
 * screening-worker). 그 전이는 REVIEW_REQUIRED 가드가 붙은 native @Modifying 이라
 * @Immutable 엔티티의 dirty-write 로는 표현할 수 없다(경합·재결정 0행 = 충돌).
 */
public interface ReviewItemRepository extends Repository<AnalysisItemEntity, String> {

	List<AnalysisItemEntity> findByStatusOrderByReceivedAtAsc(String status, Limit limit);

	Optional<AnalysisItemEntity> findById(String explanationResultId);

	/**
	 * 검수 결정 전이 — REVIEW_REQUIRED 에서만 허용(동시 결정·재결정은 0행 = 충돌).
	 * @return 전이된 행 수(1 = 성공, 0 = 충돌).
	 */
	@Transactional
	@Modifying
	@Query(value = """
			UPDATE analysis_item SET status = :status, updated_at = now()
			WHERE explanation_result_id = :id AND status = 'REVIEW_REQUIRED'
			""", nativeQuery = true)
	int decide(@Param("id") String explanationResultId, @Param("status") String decidedStatus);
}
