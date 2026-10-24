package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.ScreeningRule;

/**
 * 점검 룰 인스턴스 응답 — tenant-console-ui screening 타입과 1:1 camelCase.
 * 도메인 record(ScreeningRule)와 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record ScreeningRuleResponse(long id, String ruleType, String text, String action,
		boolean enabled) {

	public static ScreeningRuleResponse from(ScreeningRule r) {
		return new ScreeningRuleResponse(r.id(), r.ruleType(), r.text(), r.action(), r.enabled());
	}
}
