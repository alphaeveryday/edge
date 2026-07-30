package com.edge.tenantconsole.model;

import java.time.OffsetDateTime;

/**
 * 반입(수신) 상태 도메인 표현(ALPHA-607) — 원장(analysis_item) 반입 흐름을 집계한다.
 * dto(FeedStatusResponse)가 lastReceivedAt 을 상대 시각 문구로 번역한다.
 */
public record FeedStatus(String state, OffsetDateTime lastReceivedAt, long todayReceived) {

	public static final String NORMAL = "NORMAL";
	public static final String DELAYED = "DELAYED";
	public static final String STOPPED = "STOPPED";
}
