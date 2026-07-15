package com.edge.tenantsync.dto;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;
import java.util.Map;

/**
 * 번들 엔트리 하나 = 테넌트별 전달 레코드 하나 (docs/contracts/event-bundle-schema.md).
 * NEW·CORRECTION 은 본체 전체를, INVALIDATION 은 대상 참조·사유만 담는다(빈 필드는 NON_NULL 생략).
 * source_events·evidences 요소 형상은 [합의 필요 — 경계면 컬럼 선정]이라 Map 으로 둔다.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record BundleEntry(
		long cursor,
		DeliveryType deliveryType,
		String targetExplanationResultId,
		String reason,
		ExplanationResult explanationResult,
		ExplanationRun explanationRun,
		List<Map<String, Object>> sourceEvents,
		List<Map<String, Object>> evidences
) {

	public static BundleEntry newResult(long cursor, ExplanationResult result, ExplanationRun run,
			List<Map<String, Object>> sourceEvents, List<Map<String, Object>> evidences) {
		return new BundleEntry(cursor, DeliveryType.NEW, null, null, result, run, sourceEvents, evidences);
	}

	public static BundleEntry correction(long cursor, String targetExplanationResultId, String reason,
			ExplanationResult republished, ExplanationRun run) {
		return new BundleEntry(cursor, DeliveryType.CORRECTION, targetExplanationResultId, reason,
				republished, run, List.of(), List.of());
	}

	public static BundleEntry invalidation(long cursor, String targetExplanationResultId, String reason) {
		return new BundleEntry(cursor, DeliveryType.INVALIDATION, targetExplanationResultId, reason,
				null, null, null, null);
	}
}
