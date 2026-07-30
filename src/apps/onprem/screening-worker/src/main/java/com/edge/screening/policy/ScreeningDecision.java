package com.edge.screening.policy;

import java.util.List;

/**
 * 판정 결과 — 최종 상태(AUTO_PUBLISHED/REVIEW_REQUIRED/BLOCKED)와 screening_check 에
 * 남길 근거 행 목록. ruleId NULL = 룰 무관 판정(자동 제공 스위치 OFF·출처 수 미달·청정 통과).
 */
public record ScreeningDecision(String status, List<Check> checks) {

	public record Check(Long ruleId, String result, String matchedText) {
	}
}
