package com.edge.tenantconsole.model;

/**
 * 점검 룰 인스턴스 도메인 형(ALPHA-756) — screening_rule 행의 화면 투영. 금칙어
 * 전용 표면(BannedWord)과 달리 rule_type 을 가리지 않는다: 콘솔이 BANNED_WORD 만
 * 보여주던 동안 SINGLE_SOURCE·ASSERTIVE_EXPRESSION 인스턴스는 활성이어도 화면에
 * 없어 운영자가 모르는 판정 근거였다. text 는 params.text(없는 룰 타입은 null).
 */
public record ScreeningRule(long id, String ruleType, String text, String action, boolean enabled) {
}
