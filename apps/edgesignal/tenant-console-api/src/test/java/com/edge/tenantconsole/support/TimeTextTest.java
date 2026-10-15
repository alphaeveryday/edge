package com.edge.tenantconsole.support;

import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 표시용 시각 문구 규칙 검증(ALPHA-607) — 상대 시각 버킷(분/시간/일)과 KST 절대·근거
 * 표시 포맷. 상대 문구는 now 를 고정해 경계값을 결정적으로 검증한다(now() 직접 호출은 불가).
 */
class TimeTextTest {

	private static final OffsetDateTime NOW = OffsetDateTime.parse("2026-07-11T12:00:00+09:00");

	@Test
	void 상대_시각은_분_시간_일_버킷으로_표시된다() {
		assertThat(TimeText.relativeTo(NOW.minusSeconds(30), NOW)).isEqualTo("방금 전");
		assertThat(TimeText.relativeTo(NOW.minusMinutes(9), NOW)).isEqualTo("9분 전");
		assertThat(TimeText.relativeTo(NOW.minusMinutes(59), NOW)).isEqualTo("59분 전");
		assertThat(TimeText.relativeTo(NOW.minusHours(3), NOW)).isEqualTo("3시간 전");
		assertThat(TimeText.relativeTo(NOW.minusHours(23), NOW)).isEqualTo("23시간 전");
		assertThat(TimeText.relativeTo(NOW.minusDays(2), NOW)).isEqualTo("2일 전");
	}

	@Test
	void 절대_근거_시각은_KST_로_표시된다() {
		// 원장 시각(UTC 오프셋)도 KST 로 통일해 표시한다
		OffsetDateTime utc = OffsetDateTime.parse("2026-07-11T01:42:00Z");
		assertThat(TimeText.absolute(utc)).isEqualTo("2026-07-11 10:42 KST");
		// 근거 시각도 KST 명시(ALPHA-922) — 검수 상세(kstMinute)와 같은 모양이어야 한다.
		assertThat(TimeText.doc(utc)).isEqualTo("2026-07-11 10:42 KST");
		assertThat(TimeText.doc(null)).isEqualTo("—");
	}
}
