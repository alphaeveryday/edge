package com.edge.tenantsync.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.util.List;
import java.util.Map;

/**
 * 번들 엔트리 하나 = 테넌트별 전달 레코드 하나 (docs/contracts/event-bundle-schema.md).
 * NEW·CORRECTION 은 본체 전체를, INVALIDATION 은 대상 참조·사유만 담는다(빈 필드는 NON_NULL 생략).
 * source_events·evidences 컬럼은 확정됐으나(ALPHA-395) 조립 조인 미구현(ALPHA-363)이라 Map 으로 둔다.
 * 와이어 필드는 snake_case — record 필드에 @JsonNaming 적용. sourceEvents/evidences 의 Map 키는
 * naming 전략 대상이 아니다(Jackson 은 Map 키를 변환하지 않는다) — 현재 빈 배열이며, ALPHA-363
 * 조립 도입 시 그 조립부가 snake_case 키를 직접 내야 계약을 지킨다(계약 테스트로 가드 필요).
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
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
