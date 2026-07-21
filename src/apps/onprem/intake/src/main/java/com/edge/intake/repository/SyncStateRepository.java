package com.edge.intake.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/**
 * sync_state — committed cursor 의 durable 저장(단일 행). 권위 재개점은 이 값이다
 * (sync-protocol.md: 2모듈 표준에서 Pull 재개점 = Intake 의 committed cursor).
 */
@Repository
public class SyncStateRepository {

	private final JdbcTemplate jdbc;

	public SyncStateRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	public long lastCursor() {
		Long cursor = jdbc.queryForObject("SELECT last_cursor FROM sync_state", Long.class);
		if (cursor == null) {
			throw new IllegalStateException("sync_state 가 비어 있다 — baseline 마이그레이션이 단일 행을 보장해야 한다");
		}
		return cursor;
	}

	public void advance(long cursorTo) {
		jdbc.update("UPDATE sync_state SET last_cursor = ?, last_synced_at = now()", cursorTo);
	}
}
