package com.edge.intake.envelope;

import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * WHY: intake 는 봉투 cursor 범위만 판독하고(적재 키·전진값), 엔트리 의미는 건드리지 않는다(ADR-0036).
 * 계약을 어긴 번들은 조용히 넘기지 않고 실패시켜야 한다(Rule 12).
 */
class BundleEnvelopeTest {

	@Test
	@DisplayName("봉투에서 cursor_from/cursor_to 를 뽑는다")
	void cursor_범위_판독() {
		byte[] body = "{\"bundle_id\":\"x\",\"cursor_from\":4,\"cursor_to\":7,\"entries\":[]}".getBytes(UTF_8);
		BundleEnvelope.CursorRange range = BundleEnvelope.cursorRange(body);
		assertEquals(4L, range.from());
		assertEquals(7L, range.to());
	}

	@Test
	@DisplayName("엔트리가 있어도 cursor 범위만 취한다 — 의미 파싱은 Screening 몫")
	void 엔트리는_무시() {
		byte[] body = "{\"cursor_from\":1,\"cursor_to\":1,\"entries\":[{\"cursor\":1,\"delivery_type\":\"NEW\"}]}".getBytes(UTF_8);
		BundleEnvelope.CursorRange range = BundleEnvelope.cursorRange(body);
		assertEquals(1L, range.from());
		assertEquals(1L, range.to());
	}

	@Test
	@DisplayName("cursor 결측 봉투는 IllegalStateException — 삼키지 않는다")
	void cursor_결측은_실패() {
		byte[] body = "{\"bundle_id\":\"x\"}".getBytes(UTF_8);
		assertThrows(IllegalStateException.class, () -> BundleEnvelope.cursorRange(body));
	}

	@Test
	@DisplayName("비정수 cursor(1.9)는 조용히 절삭하지 않고 실패시킨다 — 멱등 키·committed cursor 불일치 방지")
	void 비정수_cursor는_실패() {
		byte[] body = "{\"cursor_from\":1.9,\"cursor_to\":3.9}".getBytes(UTF_8);
		assertThrows(IllegalStateException.class, () -> BundleEnvelope.cursorRange(body));
	}
}
