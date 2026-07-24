package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.mock.ExplanationMockStore.FeedStatus;

/**
 * 반입 상태 응답(ALPHA-513) — 피드 상태·최근 반입·오늘 반입 수. mock record(FeedStatus)와
 * 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record FeedStatusResponse(String state, String lastReceivedRelative, int todayReceived) {

	public static FeedStatusResponse from(FeedStatus s) {
		return new FeedStatusResponse(s.state(), s.lastReceivedRelative(), s.todayReceived());
	}
}
