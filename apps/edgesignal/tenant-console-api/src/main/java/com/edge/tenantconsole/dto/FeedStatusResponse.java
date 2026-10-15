package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.FeedStatus;
import com.edge.tenantconsole.support.TimeText;

/**
 * 반입 상태 응답(ALPHA-607) — 피드 상태·최근 반입·오늘 반입 수. 최근 반입 시각은 상대
 * 문구("9분 전")로 번역하고, 반입 이력이 없으면(lastReceivedAt null) "—"를 준다.
 */
public record FeedStatusResponse(String state, String lastReceivedRelative, long todayReceived) {

	public static FeedStatusResponse from(FeedStatus s) {
		String relative = s.lastReceivedAt() == null ? "—" : TimeText.relative(s.lastReceivedAt());
		return new FeedStatusResponse(s.state(), relative, s.todayReceived());
	}
}
