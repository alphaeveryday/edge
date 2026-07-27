package com.edge.screening.policy;

import java.util.List;

/**
 * 활성 정책 버전의 판정용 스냅샷 — policy_version(활성 최대 1건) + 소속 enabled 룰.
 * minSourceCount 는 NULL = 출처 수 조건 없음(DDL 주석).
 */
public record ActivePolicy(long policyVersionId, boolean autoPublishEnabled, Integer minSourceCount,
		List<PolicyRule> rules) {
}
