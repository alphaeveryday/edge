package com.edge.syncagent.dto;

/**
 * 업스트림 번들 응답의 무변형 표현. {@code empty}=204(새 이벤트 없음),
 * 아니면 {@code body}(응답 바이트 그대로)와 {@code checksum}(X-Bundle-Checksum 헤더값).
 */
public record BundleResponse(boolean empty, byte[] body, String checksum) {

	public static BundleResponse noContent() {
		return new BundleResponse(true, null, null);
	}

	public static BundleResponse of(byte[] body, String checksum) {
		return new BundleResponse(false, body, checksum);
	}
}
