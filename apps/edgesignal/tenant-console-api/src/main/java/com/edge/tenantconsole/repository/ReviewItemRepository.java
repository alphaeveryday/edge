package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.AnalysisItemEntity;
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

	/**
	 * 검수 목록 페이지 — 최근 수신 순(ALPHA-914). 수신 시각이 같은 행은 id 보조 정렬로
	 * 페이지 경계를 안정화한다(무한 스크롤에서 중복·누락 방지).
	 */
	@Query(value = """
			SELECT * FROM analysis_item WHERE status = :status
			ORDER BY received_at DESC, explanation_result_id DESC
			LIMIT :limit OFFSET :offset
			""", nativeQuery = true)
	List<AnalysisItemEntity> pageByStatus(@Param("status") String status,
			@Param("limit") int limit, @Param("offset") int offset);

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
