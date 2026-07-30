package com.edge.screening.repository;

import com.edge.screening.entity.ScreeningCheck;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.Repository;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

/**
 * 점검 결과 append — append-only 원장(UPDATE/DELETE 없음, 스키마 COMMENT). 복합 FK
 * (fk_screening_check_rule_in_version)가 룰-버전 교차 연결을 DB 에서 차단한다.
 */
public interface ScreeningCheckRepository extends Repository<ScreeningCheck, Long> {

	@Modifying
	@Transactional
	@Query(value = """
			INSERT INTO screening_check (analysis_item_id, policy_version_id, screening_rule_id, result, matched_text)
			VALUES (:analysisItemId, :policyVersionId, :screeningRuleId, :result, :matchedText)
			""", nativeQuery = true)
	void append(@Param("analysisItemId") String analysisItemId,
			@Param("policyVersionId") long policyVersionId,
			@Param("screeningRuleId") Long screeningRuleId,
			@Param("result") String result,
			@Param("matchedText") String matchedText);
}
