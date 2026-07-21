package com.edge.intake.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 온프렘 DB 의 received_bundle·sync_state 접근. cursor(committed) 는 Intake 소유(ADR-0036) —
 * 번들 적재와 cursor 전진을 <b>한 트랜잭션</b>으로 묶어 커밋 뒤에만 전진시킨다(skip 유실 방지).
 */
@Component
public class ReceivedBundleStore {

	private static final String INSERT_BUNDLE = """
			INSERT INTO received_bundle (cursor_from, cursor_to, checksum, body)
			VALUES (?, ?, ?, ?)
			ON CONFLICT (cursor_from) DO NOTHING
			""";

	// committed cursor 는 '실제 적재된' 번들의 최대 cursor_to 다 — 넘겨받은 cursor_to 가 아니다.
	// ON CONFLICT 로 INSERT 가 스킵된(다른 범위가 이미 있는) 번들의 cursor_to 로 전진하면
	// 미적재 이벤트를 영구 skip(유실)한다. MAX(cursor_to) 는 그 경합·부분 겹침을 흡수한다(ADR-0036).
	private static final String ADVANCE_CURSOR = """
			UPDATE sync_state
			   SET last_cursor = (SELECT COALESCE(MAX(cursor_to), 0) FROM received_bundle),
			       last_synced_at = now()
			 WHERE single_row = TRUE
			""";

	private final JdbcTemplate jdbc;

	public ReceivedBundleStore(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	public long lastCursor() {
		Long cursor = jdbc.queryForObject(
				"SELECT last_cursor FROM sync_state WHERE single_row = TRUE", Long.class);
		return cursor == null ? 0L : cursor;
	}

	/** 검증된 번들 원본을 멱등 적재하고 committed cursor 를 전진시킨다(같은 트랜잭션). */
	@Transactional
	public void store(long cursorFrom, long cursorTo, String checksum, byte[] body) {
		jdbc.update(INSERT_BUNDLE, cursorFrom, cursorTo, checksum, body);
		jdbc.update(ADVANCE_CURSOR);
	}
}
