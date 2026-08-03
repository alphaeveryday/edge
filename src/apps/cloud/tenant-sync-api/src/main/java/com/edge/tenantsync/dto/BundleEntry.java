package com.edge.tenantsync.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.util.List;
import java.util.Map;

/**
 * 번들 엔트리 하나 = 테넌트별 전달 레코드 하나 (docs/contracts/event-bundle-schema.md).
 * NEW 는 본체 전체를, INVALIDATION 은 대상 참조·사유만 담는다(빈 필드는 NON_NULL 생략).
 * evidences 는 조립 조인(ALPHA-718)이 채운다 — 형상은 EvidenceItem. sourceEvents 는 컬럼
 * 확정(ALPHA-395)에도 온프렘 소비자가 없어 빈 배열이다(이벤트 타임라인 UI 도입 시 조립) —
 * 그때까지 Map 으로 둔다. 와이어 필드는 snake_case — record 필드에 @JsonNaming 적용.
 * sourceEvents 의 Map 키는 naming 전략 대상이 아니다(Jackson 은 Map 키를 변환하지 않는다)
 * — 조립 도입 시 그 조립부가 snake_case 키를 직접 내야 계약을 지킨다(계약 테스트로 가드 필요).
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
		List<EvidenceItem> evidences
) {

	public static BundleEntry newResult(long cursor, ExplanationResult result, ExplanationRun run,
			List<Map<String, Object>> sourceEvents, List<EvidenceItem> evidences) {
		return new BundleEntry(cursor, DeliveryType.NEW, null, null, result, run, sourceEvents, evidences);
	}

	public static BundleEntry invalidation(long cursor, String targetExplanationResultId, String reason) {
		return new BundleEntry(cursor, DeliveryType.INVALIDATION, targetExplanationResultId, reason,
				null, null, null, null);
	}
}
