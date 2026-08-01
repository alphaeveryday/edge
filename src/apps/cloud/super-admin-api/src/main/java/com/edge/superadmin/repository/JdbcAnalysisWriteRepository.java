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
 * tenants 전용).
 *
 * <p><b>explanation_result 를 쓰지 않는다.</b> 그 원장은 analysis-engine(pipeline) 소유이고
 * super-admin-api 는 reader 다 — 소유자 밖 쓰기는 단일 writer 규약 위반(ADR-0005·implementation §4).
 * 정정도 원본 결과를 덮지 않는다: 운영자 작업은 전부 super-admin-api 소유 원장
 * {@code admin_activity_log}(ALPHA-424 Admin Activity Log)에만 append 하고, 정정된 본문·제외 여부는
 * 읽기가 이 원장에서 오버레이한다({@link JdbcAnalysisRepository}). 원본 explanation_result 는
 * 보존되고, 테넌트 전파는 INVALIDATION 발번(ALPHA-440)만 후속이다 — CORRECTION 전달은
 * 폐지됐다(ADR-0044). 감사 열람 API 는
 * 없다(super-admin-console.md: UI-less, DB 보존).
 */
@Repository
public class JdbcAnalysisWriteRepository implements AnalysisWriteRepository {

	/** 런의 현재 노출 본문 = 최신 정정본(있으면) 아니면 원장 원본. 감사 "before" 와 정정 가능 판정에 쓴다. */
	private static final String EFFECTIVE_SUMMARY_SQL = """
			SELECT COALESCE(
			       (SELECT details ->> 'after' FROM admin_activity_log
			         WHERE target_type = 'ANALYSIS_RUN' AND target_id = er.explanation_run_id
			           AND action = 'ANALYSIS_RESULT_CORRECTED'
			         ORDER BY activity_id DESC LIMIT 1),
			       res.summary) AS effective_summary
			  FROM explanation_result res
			  JOIN explanation_run er ON er.explanation_run_id = res.explanation_run_id
			 WHERE er.explanation_run_id = ?
			""";

	private final JdbcTemplate jdbc;
	private final ObjectMapper objectMapper;

	public JdbcAnalysisWriteRepository(JdbcTemplate jdbc, ObjectMapper objectMapper) {
		this.jdbc = jdbc;
		this.objectMapper = objectMapper;
	}

	@Override
	@Transactional
	public boolean correct(String runId, String result, String reason, SessionOperator actor) {
		// 같은 런의 동시 정정을 직렬화한다 — before(현재 노출 본문) 조회와 append 사이에 다른
		// 정정이 끼면 감사 체인(원본→A→B)이 실제 전이 순서를 잃는다. xact 스코프 advisory lock 이라
		// 커밋 시 자동 해제되고, 소유하지 않은 도메인 테이블을 잠그지 않는다.
		jdbc.query("SELECT pg_advisory_xact_lock(hashtext(?)::bigint)", rs -> { },
				"analysis-correct:" + runId);
		// 결과 행이 있어야 정정 대상이다 — 없으면(미완·미존재 런) false(404). before 는 현재
		// 노출 본문(최신 정정본 우선)이라 연속 정정의 감사 체인이 실제 전이를 재현한다.
		List<String> before = jdbc.queryForList(EFFECTIVE_SUMMARY_SQL, String.class, runId);
		if (before.isEmpty()) {
			return false;
		}
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
