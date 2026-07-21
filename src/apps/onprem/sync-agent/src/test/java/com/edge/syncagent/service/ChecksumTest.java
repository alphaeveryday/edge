package com.edge.syncagent.service;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 체크섬 대조 규율 — 계약(event-bundle-schema.md): 수신 바이트 그대로 SHA-256.
 * 한 바이트라도 변형되면 불일치가 감지돼야 위변조·전송 오염이 걸러진다.
 */
class ChecksumTest {

	private static final byte[] BODY = "{\"cursor_from\":1}".getBytes(StandardCharsets.UTF_8);
	// printf '{"cursor_from":1}' | shasum -a 256
	private static final String VALID =
			"sha256=4323f97d6ab969490e1b2626736497907378c8394fa63679df66f48500462bde";

	@Test
	void 수신_바이트와_일치하는_체크섬만_통과한다() {
		assertThat(BundleRelayService.checksumMatches(BODY, VALID)).isTrue();
	}

	@Test
	void 바이트가_한_글자라도_다르면_불일치다() {
		byte[] tampered = BODY.clone();
		tampered[2] ^= 1;
		assertThat(BundleRelayService.checksumMatches(tampered, VALID)).isFalse();
	}

	@Test
	void 형식_위반_헤더는_불일치다() {
		assertThat(BundleRelayService.checksumMatches(BODY, null)).isFalse();
		assertThat(BundleRelayService.checksumMatches(BODY, "md5=abc")).isFalse();
	}
}
