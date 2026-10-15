package com.edge.tenantconsole.model;

/**
 * 금칙어 도메인 형(ALPHA-438) — screening_rule(BANNED_WORD) 행의 화면 투영.
 * id = screening_rule_id(활성 버전 내 식별), active = enabled, registeredAt = 최초 등록일
 * (버전 복사 시 created_at 을 보존해 유지된다).
 */
public record BannedWord(long id, String text, String action, boolean active,
		String registeredAt) {
}
