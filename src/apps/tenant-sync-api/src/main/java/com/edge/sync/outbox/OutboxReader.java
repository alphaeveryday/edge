package com.edge.sync.outbox;

import com.edge.sync.bundle.BundleEntry;

import java.util.List;

/**
 * 테넌트 outbox 를 cursor 순으로 읽는다 — 번들 생성의 유일한 소스.
 * 구현 교체 지점: 현재는 인메모리 스텁, Cloud Event Store 스키마(진기 Flyway) 확정 후
 * tenant_outbox JDBC 구현으로 교체한다 (docs/contracts/event-bundle-schema.md).
 */
public interface OutboxReader {

	/** {@code afterCursor} 초과분을 cursor 오름차순으로 최대 {@code limit} 건 반환한다. */
	List<BundleEntry> readAfter(String tenantId, long afterCursor, int limit);
}
