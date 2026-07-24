package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.mock.ScreeningMockStore.AutoPublishCriteria;

/**
 * 자동 제공 기준 응답(ALPHA-513) — 최소 근거 수·최대 위험도. mock record
 * (AutoPublishCriteria)와 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record CriteriaResponse(int minSources, String maxRisk) {

	public static CriteriaResponse from(AutoPublishCriteria c) {
		return new CriteriaResponse(c.minSources(), c.maxRisk());
	}
}
