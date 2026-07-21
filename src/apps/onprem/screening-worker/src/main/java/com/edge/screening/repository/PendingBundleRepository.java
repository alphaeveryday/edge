package com.edge.screening.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * received_bundle 의 미점검분 조회·마킹 — Intake↔Screening 의 DB 매개 핸드오프.
 * 원본 컬럼(cursor·checksum·body)은 불변 — 이 저장소는 screened_at 만 갱신한다.
 */
@Repository
public class PendingBundleRepository {

	public record PendingBundle(long cursorFrom, byte[] body) {
	}

	private final JdbcTemplate jdbc;

	public PendingBundleRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	public List<PendingBundle> findUnscreened(int limit) {
		return jdbc.query("""
				SELECT cursor_from, body FROM received_bundle
				WHERE screened_at IS NULL ORDER BY cursor_from LIMIT ?
				""",
				(rs, n) -> new PendingBundle(rs.getLong("cursor_from"), rs.getBytes("body")),
				limit);
	}

	public void markScreened(long cursorFrom) {
		jdbc.update("UPDATE received_bundle SET screened_at = now() WHERE cursor_from = ?", cursorFrom);
	}
}
