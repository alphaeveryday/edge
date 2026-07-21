package com.edge.syncagent.service;

import com.edge.common.exception.GeneralException;
import com.edge.syncagent.error.SyncAgentErrorStatus;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import org.springframework.stereotype.Component;

/**
 * 수신 응답 바이트열 그대로 SHA-256 을 계산해 X-Bundle-Checksum 헤더값과 대조한다.
 * 재직렬화하지 않는다 — 필드 순서·공백이 달라지면 계약(sync-protocol.md §무결성)이 깨진다.
 */
@Component
public class ChecksumVerifier {

	private static final String PREFIX = "sha256=";

	/** 불일치·헤더 결측 시 GeneralException(→ 502) 을 던진다. */
	public void verify(byte[] body, String checksumHeader) {
		if (checksumHeader == null || checksumHeader.isBlank()) {
			throw new GeneralException(SyncAgentErrorStatus.MISSING_CHECKSUM);
		}
		String computed = PREFIX + HexFormat.of().formatHex(sha256(body));
		if (!computed.equals(checksumHeader)) {
			throw new GeneralException(SyncAgentErrorStatus.CHECKSUM_MISMATCH);
		}
	}

	private static byte[] sha256(byte[] body) {
		try {
			return MessageDigest.getInstance("SHA-256").digest(body);
		} catch (NoSuchAlgorithmException e) {
			// SHA-256 은 JRE 표준 — 이 경로는 도달 불가.
			throw new IllegalStateException("SHA-256 unavailable", e);
		}
	}
}
