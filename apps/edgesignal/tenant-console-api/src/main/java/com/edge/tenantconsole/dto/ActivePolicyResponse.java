package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.ActivePolicy;

import java.util.List;

/**
 * 활성 정책 응답 — 점검 처리 기준 화면이 이 하나로 표를 그린다(ALPHA-762).
 * 도메인 record 와 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record ActivePolicyResponse(boolean published, Integer versionNo, boolean autoPublishEnabled,
		Integer minSources, String minConfidence, List<ScreeningRuleResponse> rules) {

	public static ActivePolicyResponse from(ActivePolicy p) {
		return new ActivePolicyResponse(p.published(), p.versionNo(), p.autoPublishEnabled(),
				p.minSources(), p.minConfidence(),
				p.rules().stream().map(ScreeningRuleResponse::from).toList());
	}
}
