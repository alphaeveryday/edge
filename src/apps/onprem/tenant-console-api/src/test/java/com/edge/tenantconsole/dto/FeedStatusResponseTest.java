package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.FeedStatus;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 반입 상태 응답 번역 검증(ALPHA-607) — 최근 반입 시각을 상대 문구로 바꾸고, 반입 이력이
 * 없으면 "—"를 준다(시각처럼 그리지 않는다).
 */
class FeedStatusResponseTest {

	@Test
	void 최근_반입은_상대_문구로_번역된다() {
		FeedStatusResponse res = FeedStatusResponse.from(
				new FeedStatus(FeedStatus.NORMAL, OffsetDateTime.now().minusMinutes(9), 128));

		assertThat(res.state()).isEqualTo("NORMAL");
		assertThat(res.todayReceived()).isEqualTo(128);
		assertThat(res.lastReceivedRelative()).isEqualTo("9분 전");
	}

	@Test
	void 반입_이력이_없으면_상대_시각은_대시다() {
		FeedStatusResponse res = FeedStatusResponse.from(
				new FeedStatus(FeedStatus.STOPPED, null, 0));

		assertThat(res.state()).isEqualTo("STOPPED");
		assertThat(res.todayReceived()).isEqualTo(0);
		assertThat(res.lastReceivedRelative()).isEqualTo("—");
	}
}
