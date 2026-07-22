package com.edge.tenantconsole.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Repository;

import java.util.Optional;

/**
 * member 원장 접근 — writer = tenant-console-api(스키마 COMMENT). 데모 자체 계정
 * 경로(ADR-0025): 관리자 직접 등록만 있고 셀프 가입·초대 흐름은 없다.
 */
@Repository
public class MemberRepository {

	public record Member(
			long memberId,
			String email,
			String name,
			String role,
			boolean active,
			String passwordHash
	) {
	}

	private final JdbcTemplate jdbc;

	public MemberRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	public Optional<Member> findActiveByEmail(String email) {
		return jdbc.query("""
				SELECT member_id, email, name, role, is_active, password_hash
				FROM member WHERE email = ? AND is_active
				""", rowMapper(), email).stream().findFirst();
	}

	public long count() {
		Long n = jdbc.queryForObject("SELECT count(*) FROM member", Long.class);
		return n == null ? 0 : n;
	}

	public void insert(String email, String name, String role, String passwordHash) {
		jdbc.update("""
				INSERT INTO member (email, name, role, password_hash)
				VALUES (?, ?, ?, ?)
				""", email, name, role, passwordHash);
	}

	public void touchLastLogin(long memberId) {
		jdbc.update("UPDATE member SET last_login_at = now() WHERE member_id = ?", memberId);
	}

	private RowMapper<Member> rowMapper() {
		return (rs, n) -> new Member(
				rs.getLong("member_id"),
				rs.getString("email"),
				rs.getString("name"),
				rs.getString("role"),
				rs.getBoolean("is_active"),
				rs.getString("password_hash"));
	}
}
