package com.edge.tenantconsole.repository;

import com.edge.tenantconsole.entity.ScreeningRuleEntity;
import org.springframework.data.repository.Repository;

import java.util.List;

/**
 * screening_rule 발행·조회 — 룰은 버전과 함께 불변(ADR-0018)이라 쓰기는 발행 시
 * 복사 INSERT(save)뿐이다. 조회는 화면 목록용 전체(비활성 포함) — 평가기(worker)는
 * 자기 repository 로 enabled 만 읽는다.
 */
public interface ScreeningRuleRepository extends Repository<ScreeningRuleEntity, Long> {

	List<ScreeningRuleEntity> findByPolicyVersionIdOrderByScreeningRuleId(long policyVersionId);

	ScreeningRuleEntity save(ScreeningRuleEntity rule);

	/** 검수 사유 파생용 룰 배치 조회(ALPHA-436) — check 의 rule_id 를 rule_type 으로 해석한다. */
	java.util.List<ScreeningRuleEntity> findByScreeningRuleIdIn(java.util.Collection<Long> ruleIds);
}
