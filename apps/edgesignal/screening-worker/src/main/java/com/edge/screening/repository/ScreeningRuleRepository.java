package com.edge.screening.repository;

import com.edge.screening.entity.ScreeningRule;
import org.springframework.data.repository.Repository;

import java.util.List;

/**
 * 활성 정책 버전의 enabled 룰 조회 — uq_screening_rule_version_pair(선두 policy_version_id)
 * B-tree 가 지원한다. 순서는 등록순(id) 고정 — 판정 결과가 조회 순서에 흔들리지 않게.
 */
public interface ScreeningRuleRepository extends Repository<ScreeningRule, Long> {

	List<ScreeningRule> findByPolicyVersionIdAndEnabledTrueOrderByScreeningRuleId(long policyVersionId);
}
