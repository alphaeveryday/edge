package com.edge.screening.policy;

/**
 * 활성 정책의 룰 인스턴스(ADR-0018 Rule Instance) — screening_rule 행의 판정용 투영.
 * text 는 params.text(BANNED_WORD·ASSERTIVE_EXPRESSION 의 매칭 대상 표현), SINGLE_SOURCE 는 null.
 */
public record PolicyRule(long ruleId, String ruleType, String text, String action) {
}
