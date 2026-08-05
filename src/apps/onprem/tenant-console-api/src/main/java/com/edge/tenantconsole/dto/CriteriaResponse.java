package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.AutoPublishCriteria;

/**
 * 자동 제공 기준 응답 — 자동 제공 스위치·최소 근거 수·최소 확신도. 도메인 record
 * (AutoPublishCriteria)와 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 * autoPublishEnabled 는 조회·변경 양쪽에 있다(CriteriaPatchRequest 동명 필드 — ALPHA-756).
 */
public record CriteriaResponse(boolean published, boolean autoPublishEnabled,
		Integer minSources, String minConfidence) {

	public static CriteriaResponse from(AutoPublishCriteria c) {
		return new CriteriaResponse(c.published(), c.autoPublishEnabled(), c.minSources(),
				c.minConfidence());
	}
}
