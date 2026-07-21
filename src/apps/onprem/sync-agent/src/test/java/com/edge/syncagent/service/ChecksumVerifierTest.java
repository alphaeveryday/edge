package com.edge.syncagent.service;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.edge.common.exception.GeneralException;
import java.security.MessageDigest;
import java.util.HexFormat;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 체크섬 검증은 DMZ 신뢰경계의 핵심 가드다(ADR-0036) — 변조/결측 응답이 내부망으로 흘러선 안 된다.
 * WHY: 이 검증이 뚫리면 온프렘이 무결성 미확인 데이터를 적재하게 된다(Rule 9).
 */
class ChecksumVerifierTest {

	private final ChecksumVerifier verifier = new ChecksumVerifier();

	private static String checksumOf(byte[] body) {
		try {
			return "sha256=" + HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(body));
		} catch (Exception e) {
			throw new IllegalStateException(e);
		}
	}

	@Test
	@DisplayName("수신 바이트 해시가 헤더와 일치하면 통과한다")
	void 정확한_체크섬은_통과() {
		byte[] body = "{\"bundle_id\":\"x\",\"cursor_to\":3}".getBytes(UTF_8);
		assertDoesNotThrow(() -> verifier.verify(body, checksumOf(body)));
	}

	@Test
	@DisplayName("바이트가 한 글자라도 달라 해시가 안 맞으면 502(CHECKSUM_MISMATCH)")
	void 불일치_체크섬은_거부() {
		byte[] body = "abc".getBytes(UTF_8);
		String wrong = "sha256=" + "0".repeat(64);
		GeneralException ex = assertThrows(GeneralException.class, () -> verifier.verify(body, wrong));
		assertEquals("SYNCAGENT5021", ex.getErrorReasonHttpStatus().getCode());
	}

	@Test
	@DisplayName("업스트림 200인데 체크섬 헤더가 없으면 502(MISSING_CHECKSUM) — 계약 위반은 통과시키지 않는다")
	void 헤더_결측은_거부() {
		GeneralException nullHeader = assertThrows(GeneralException.class,
				() -> verifier.verify("abc".getBytes(UTF_8), null));
		assertEquals("SYNCAGENT5022", nullHeader.getErrorReasonHttpStatus().getCode());
		assertThrows(GeneralException.class, () -> verifier.verify("abc".getBytes(UTF_8), "  "));
	}
}
