package com.edge.intake.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.time.OffsetDateTime;

/**
 * 동기화 상태(sync_state) — committed cursor 의 durable 저장(단일 행). 권위 재개점이 이 값이다
 * (sync-protocol.md). 스키마 SSOT 는 Flyway(libs/schema, ADR-0038)라 이 엔티티는 검증·조회용이며,
 * 전진(advance)은 리포지토리의 native @Query 로 나간다(단일 행 UPDATE) — @Immutable 은 붙이지 않는다.
 */
@Entity
@Table(name = "sync_state")
public class SyncState {

	// single_row BOOLEAN PRIMARY KEY DEFAULT TRUE — 단일 행 보장용 PK.
	@Id
	private Boolean singleRow;

	private Long lastCursor;

	private OffsetDateTime lastSyncedAt;

	protected SyncState() {
	}

	public Boolean getSingleRow() {
		return singleRow;
	}

	public Long getLastCursor() {
		return lastCursor;
	}

	public OffsetDateTime getLastSyncedAt() {
		return lastSyncedAt;
	}
}
