package com.edge.sync.bundle;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;
import java.util.UUID;

/**
 * 번들 엔트리 하나 = outbox 전달 레코드 하나.
 * NEW·CORRECTION 은 전체 상태(full snapshot) — 온프렘은 도메인 ID 멱등 upsert 만 하면 된다.
 * INVALIDATION 은 대상 참조·사유만 담는다(event·candidates·evidences 없음 → NON_NULL 로 생략).
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record BundleEntry(
		long cursor,
		DeliveryType deliveryType,
		UUID targetEventId,
		String reason,
		EventPayload event,
		List<ExplanationCandidate> candidates,
		List<Evidence> evidences
) {

	public static BundleEntry newEvent(long cursor, EventPayload event,
			List<ExplanationCandidate> candidates, List<Evidence> evidences) {
		return new BundleEntry(cursor, DeliveryType.NEW, null, null, event, candidates, evidences);
	}

	public static BundleEntry correction(long cursor, UUID targetEventId, String reason,
			EventPayload event, List<ExplanationCandidate> candidates, List<Evidence> evidences) {
		return new BundleEntry(cursor, DeliveryType.CORRECTION, targetEventId, reason, event, candidates, evidences);
	}

	public static BundleEntry invalidation(long cursor, UUID targetEventId, String reason) {
		return new BundleEntry(cursor, DeliveryType.INVALIDATION, targetEventId, reason, null, null, null);
	}
}
