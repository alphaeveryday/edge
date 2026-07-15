package com.edge.tenantsync.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/** Event Bundle 봉투 — 테넌트별 전달 레코드를 cursor 순으로 묶은 전송 단위. tenant_id 는 BIGINT(docs/contracts/event-bundle-schema.md). */
public record EventBundle(
		UUID bundleId,
		long tenantId,
		Instant generatedAt,
		long cursorFrom,
		long cursorTo,
		List<BundleEntry> entries
) {

	public static EventBundle of(long tenantId, List<BundleEntry> entries) {
		if (entries.isEmpty()) {
			throw new IllegalArgumentException("빈 번들은 만들 수 없다 — 신규 없음은 204 로 표현한다");
		}
		return new EventBundle(
				UUID.randomUUID(), // 응답 인스턴스 식별용. 멱등 키는 (tenant_id, cursor) — 계약 참조.
				tenantId,
				Instant.now(),
				entries.getFirst().cursor(),
				entries.getLast().cursor(),
				entries
		);
	}
}
