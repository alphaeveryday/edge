package com.edge.intake.dto;

/**
 * sync-agent 에서 받은 번들. {@code empty}=204(새 이벤트 없음),
 * 아니면 검증 통과 {@code body}(바이트 그대로)와 {@code checksum}(헤더값 그대로).
 */
public record FetchedBundle(boolean empty, byte[] body, String checksum) {

	public static FetchedBundle noContent() {
		return new FetchedBundle(true, null, null);
	}

	public static FetchedBundle of(byte[] body, String checksum) {
		return new FetchedBundle(false, body, checksum);
	}
}
