package com.edge.intake.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/**
 * received_bundle — Raw Event Store. 수신 바이트 원본을 불변 보존한다.
 * dedup 은 cursor 기준(ON CONFLICT cursor_from DO NOTHING) — 재-Pull 응답은
 * bundle_id·generated_at 이 새로 발번돼 바이트가 달라지므로 바이트로 dedup 하지
 * 않는다(sync-protocol.md 전달 보장 절).
 */
@Repository
public class ReceivedBundleRepository {

	private static final String INSERT_SQL = """
			INSERT INTO received_bundle (cursor_from, cursor_to, checksum, body)
			VALUES (?, ?, ?, ?)
			ON CONFLICT (cursor_from) DO NOTHING
			""";

	private final JdbcTemplate jdbc;

	public ReceivedBundleRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	/** @return 신규 저장 여부 — false 는 중복(무해, at-least-once). */
	public boolean save(long cursorFrom, long cursorTo, String checksum, byte[] body) {
		return jdbc.update(INSERT_SQL, cursorFrom, cursorTo, checksum, body) > 0;
	}
}
