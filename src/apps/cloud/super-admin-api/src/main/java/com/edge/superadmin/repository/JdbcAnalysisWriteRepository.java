package com.edge.superadmin.repository;

import com.edge.superadmin.auth.SessionOperator;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;

/**
 * {@link AnalysisWriteRepository} 의 JdbcTemplate 구현(ALPHA-602). 분석 도메인 읽기
 * ({@link JdbcAnalysisRepository})가 JdbcTemplate 이라 쓰기도 같은 결로 맞춘다(이 앱에서 JPA 는
 * tenants 전용).
 *
 * <p><b>무효화(ALPHA-440)는 실 전이다</b> — publication_status(WITHDRAWN)와
 * tenant_delivery(INVALIDATION 발번)를 직접 쓴다. 소유자 합의는 event-bundle-schema.md
 * "fan-out 발번기" 절. cursor 채번은 엔진의 NEW 발번과 같은 advisory
 * lock('tenant-delivery-fanout')으로 직렬화한다 — 문자열이 다르면 다른 잠금이다.
 * 감사는 {@code admin_activity_log}(ALPHA-424)에 append — 구 정정/제외/복원 오버레이는
 * ALPHA-737 로 은퇴했고 그 기록은 이 원장에 이력으로 보존된다. 감사 열람 API 는
 * 없다(super-admin-console.md: UI-less, DB 보존).
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
	public InvalidateOutcome invalidate(String runId, String reason, SessionOperator actor) {
		// 엔진 NEW 발번(eventstore.py _fanout_new)과 같은 잠금 — cursor(테넌트별 MAX+1)를
		// 뽑는 주체가 둘이라, 동시 발번이 같은 번호를 집지 못하게 한 줄로 세운다.
		jdbc.query("SELECT pg_advisory_xact_lock(hashtext(?)::bigint)", rs -> { },
				"tenant-delivery-fanout");
		// run↔result 는 UNIQUE 1:1 — 게시본이 없으면(미완·DRAFT·이미 무효화) 전이 대상이 없다.
		List<String> published = jdbc.queryForList("""
				SELECT explanation_result_id FROM explanation_result
				 WHERE explanation_run_id = ? AND publication_status = 'PUBLISHED'
				""", String.class, runId);
		if (published.isEmpty()) {
			return runExists(runId) ? InvalidateOutcome.NOT_PUBLISHED
					: InvalidateOutcome.RUN_NOT_FOUND;
		}
		String resultId = published.get(0);
		jdbc.update("""
				UPDATE explanation_result SET publication_status = 'WITHDRAWN'
				 WHERE explanation_result_id = ? AND publication_status = 'PUBLISHED'
				""", resultId);
		// INVALIDATION 발번 — CHECK(ck_tenant_delivery_payload): 본체 참조 NULL, target·reason
		// 필수. cursor 는 테넌트별 단조증가(_fanout_new 와 같은 형상). 대상은 전 테넌트가
		// 아니라 **그 결과의 NEW 를 받은 테넌트**다 — 게시 후 생성된 테넌트에 원본 없는
		// 무효화를 발번하면 "원본 미수신 무효화 = gap 에서만 발생"(sync-protocol.md) 계약이
		// 깨진다. EXISTS 제한이 테넌트별 NEW cursor < INVALIDATION cursor 를 구조적으로 보장.
		jdbc.update("""
				INSERT INTO tenant_delivery
				       (tenant_id, cursor, delivery_type, target_explanation_result_id, reason)
				SELECT t.tenant_id, COALESCE(MAX(d.cursor), 0) + 1, 'INVALIDATION', ?, ?
				  FROM tenant t LEFT JOIN tenant_delivery d ON d.tenant_id = t.tenant_id
				 WHERE EXISTS (SELECT 1 FROM tenant_delivery n
				                WHERE n.tenant_id = t.tenant_id AND n.explanation_result_id = ?)
				 GROUP BY t.tenant_id
				""", resultId, reason, resultId);
		record("ANALYSIS_INVALIDATED", runId, reason, null, actor);
		return InvalidateOutcome.INVALIDATED;
	}

	private boolean runExists(String runId) {
		return Boolean.TRUE.equals(jdbc.queryForObject(
				"SELECT EXISTS(SELECT 1 FROM explanation_run WHERE explanation_run_id = ?)",
				Boolean.class, runId));
	}

	/** 감사 레코드 1건 append. details 가 null 이면 JSONB 도 null(무효화는 전후 페이로드가 없다). */
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
