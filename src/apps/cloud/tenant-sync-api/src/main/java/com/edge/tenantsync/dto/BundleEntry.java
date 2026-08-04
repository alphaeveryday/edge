package com.edge.tenantsync.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

import java.util.List;

/**
 * 번들 엔트리 하나 = 테넌트별 전달 레코드 하나 (docs/contracts/event-bundle-schema.md).
 * NEW 는 본체 전체를, INVALIDATION 은 대상 참조·사유만 담는다(빈 필드는 NON_NULL 생략).
 * sourceEvents·evidences 는 조립 조인(ALPHA-718)이 채운다 — 형상은 SourceEventItem·
 * EvidenceItem(각 DTO 가 자기 snake_case 를 소유). 와이어 필드는 snake_case — record
 * 필드에 @JsonNaming 적용.
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
		List<SourceEventItem> sourceEvents,
		List<EvidenceItem> evidences
) {

	public static BundleEntry newResult(long cursor, ExplanationResult result, ExplanationRun run,
			List<SourceEventItem> sourceEvents, List<EvidenceItem> evidences) {
		return new BundleEntry(cursor, DeliveryType.NEW, null, null, result, run, sourceEvents, evidences);
	}

	public static BundleEntry invalidation(long cursor, String targetExplanationResultId, String reason) {
		return new BundleEntry(cursor, DeliveryType.INVALIDATION, targetExplanationResultId, reason,
				null, null, null, null);
	}
}
