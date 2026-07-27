package com.edge.tenantconsole.model;

import tools.jackson.databind.JsonNode;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * 검수 상세(ALPHA-436, 구 439 흡수) — 항목 본체 + 근거 문서(evidences JSONB) +
 * 파생 검수 사유 + 컴플라이언스 검사 결과(screening_check 전 행) + 상태 변경 이력.
 * 감사·노출 이력은 별도 메뉴가 아니라 이 상세로 확인한다(콘솔 IA).
 */
public record ReviewItemDetail(ReviewItem item, JsonNode evidences, List<String> reviewReasons,
		List<ScreeningCheckView> checks, List<StatusChange> history) {

	/** 검사 결과 한 행 — ruleType 은 룰 무관 판정(자동 제공 스위치·출처 임계)이면 null. */
	public record ScreeningCheckView(String result, String ruleType, String matchedText,
			OffsetDateTime checkedAt) {
	}

	/** 상태 전이 한 건 — actorName 은 SYSTEM 전이(자동 분기·Cloud 이벤트)면 null. */
	public record StatusChange(String fromStatus, String toStatus, String actorType,
			String actorName, String reason, OffsetDateTime occurredAt) {
	}
}
