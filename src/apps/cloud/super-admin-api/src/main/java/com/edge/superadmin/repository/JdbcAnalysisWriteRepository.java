package com.edge.superadmin.repository;

import com.edge.superadmin.auth.SessionOperator;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * {@link AnalysisWriteRepository} 의 JdbcTemplate 구현(ALPHA-602). 분석 도메인 읽기
 * ({@link JdbcAnalysisRepository})가 JdbcTemplate 이라 쓰기도 같은 결로 맞춘다(이 앱에서 JPA 는
 * tenants 전용). 도메인 전이와 감사 append 를 {@code @Transactional} 로 한 단위에 묶는다.
 *
 * <p>감사는 {@code admin_activity_log}(ALPHA-424 Admin Activity Log)에 append 한다 — 별도
 * 열람 API 는 없다(super-admin-console.md: UI-less, DB 보존). 정정/제외/복원 현재상태는 이 원장의
 * 런별 최신 액션에서 유도된다({@link JdbcAnalysisRepository} 오버레이).
 */
@Repository
public class JdbcAnalysisWriteRepository implements AnalysisWriteRepository {

	private final JdbcTemplate jdbc;
	private final ObjectMapper objectMapper;

	public JdbcAnalysisWriteRepository(JdbcTemplate jdbc, ObjectMapper objectMapper) {
		this.jdbc = jdbc;
		this.objectMapper = objectMapper;
	}

	@Override
	@Transactional
	public boolean correct(String runId, String result, String reason, SessionOperator actor) {
		// 결과 행의 현재 본문을 감사 "before" 로 확보한다 — 없으면(미완·미존재 런) 정정 대상이 없다.
		List<String> before = jdbc.queryForList(
				"SELECT summary FROM explanation_result WHERE explanation_run_id = ?",
				String.class, runId);
		if (before.isEmpty()) {
			return false;
		}
		jdbc.update("UPDATE explanation_result SET summary = ? WHERE explanation_run_id = ?",
				result, runId);
		Map<String, Object> details = new HashMap<>();
		details.put("before", before.get(0));
		details.put("after", result);
		record("ANALYSIS_RESULT_CORRECTED", runId, reason, details, actor);
		return true;
	}

	@Override
	@Transactional
	public boolean exclude(String runId, String reason, SessionOperator actor) {
		if (!runExists(runId)) {
			return false;
		}
		record("ANALYSIS_EXCLUDED", runId, reason, null, actor);
		return true;
	}

	@Override
	@Transactional
	public boolean restore(String runId, String reason, SessionOperator actor) {
		if (!runExists(runId)) {
			return false;
		}
		record("ANALYSIS_RESTORED", runId, reason, null, actor);
		return true;
	}

	private boolean runExists(String runId) {
		return Boolean.TRUE.equals(jdbc.queryForObject(
				"SELECT EXISTS(SELECT 1 FROM explanation_run WHERE explanation_run_id = ?)",
				Boolean.class, runId));
	}

	/** 감사 레코드 1건 append. details 가 null 이면 JSONB 도 null(제외/복원은 전후 페이로드가 없다). */
	private void record(String action, String targetId, String reason,
			Map<String, Object> details, SessionOperator actor) {
		String detailsJson = details == null ? null : objectMapper.writeValueAsString(details);
		jdbc.update("""
				INSERT INTO admin_activity_log
				       (actor_email, actor_name, action, target_type, target_id, reason, details)
				VALUES (?, ?, ?, 'ANALYSIS_RUN', ?, ?, ?::jsonb)
				""", actor.email(), actor.name(), action, targetId, reason, detailsJson);
	}
}
