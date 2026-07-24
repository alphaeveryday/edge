package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.mock.ScreeningMockStore.BannedWord;

/**
 * 금칙어 응답(ALPHA-513) — tenant-console-ui screening 타입과 1:1 camelCase.
 * mock record(BannedWord)와 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record BannedWordResponse(long id, String text, String risk, String action,
		boolean active, String registeredAt) {

	public static BannedWordResponse from(BannedWord w) {
		return new BannedWordResponse(w.id(), w.text(), w.risk(), w.action(), w.active(),
				w.registeredAt());
	}
}
