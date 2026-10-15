package com.edge.screening.policy;

import java.util.List;

/**
 * 활성 정책 버전의 판정용 스냅샷 — policy_version(활성 최대 1건) + 소속 enabled 룰.
 * minSourceCount 는 NULL = 출처 수 조건 없음, minConfidence 는 NULL = 확신도 조건
 * 없음(둘 다 DDL 주석의 미설정 시맨틱).
 */
public record ActivePolicy(long policyVersionId, boolean autoPublishEnabled, Integer minSourceCount,
		String minConfidence, List<PolicyRule> rules) {
}
