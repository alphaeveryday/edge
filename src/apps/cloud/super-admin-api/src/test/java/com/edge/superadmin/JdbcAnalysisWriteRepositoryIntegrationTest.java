package com.edge.superadmin;

import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.repository.AnalysisRepository;
import com.edge.superadmin.repository.AnalysisRepository.AnalysisRow;
import com.edge.superadmin.repository.AnalysisWriteRepository;
import com.edge.superadmin.repository.AnalysisWriteRepository.InvalidateOutcome;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 쓰기 원장 전이 통합 테스트(ALPHA-602) — 실 explanation_* + admin_activity_log 스키마
 * (Testcontainers + Flyway migrations-cloud)를 대상으로 정정(원본 explanation_result 불변 + 전후
 * 감사)·제외/복원(감사 append)·없는 대상(404 신호)·오버레이 왕복(쓰기→읽기 반영)을 검증한다. 손
 * 페이크만으로는 감사 JSONB·projection SQL 을 한 줄도 실행하지 않는다(Rule 9, 선례 JdbcAnalysisRepositoryIT).
 *
 * <p>@Transactional REPEATABLE_READ 스냅샷 격리는 여기서 검증하지 않는다(테스트 트랜잭션에
 * 참여하므로 안쪽 격리수준 미적용, 선례와 같은 한계).
 */
@Transactional
class JdbcAnalysisWriteRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	private static final SessionOperator OPERATOR = new SessionOperator("ops@edge.io", "운영자");

	@Autowired
	private AnalysisWriteRepository writes;

	@Autowired
	private AnalysisRepository reads;

	@Autowired
	private JdbcTemplate jdbc;

	@BeforeEach
	void seed() {
		jdbc.update("""
				INSERT INTO release_bundle (bundle_version, component_versions, component_hash, status)
				VALUES ('v1', '{"engine":"1"}'::jsonb, ?, 'DRAFT')
				""", "a".repeat(64));
		jdbc.update("""
				INSERT INTO entity (entity_id, entity_type, display_name)
				VALUES ('etf-w602', 'INSTRUMENT', 'KODEX 반도체')
				""");
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES ('etf-w602', 'XKRX', 'W602', 'ETF')
				""");
		jdbc.update("INSERT INTO etf_profile (instrument_id, etf_type) VALUES ('etf-w602', 'SECTOR')");

		// run-w1: 완료 — 결과 행까지 (정정 대상)
		seedRunChain("1", "2026-07-27", "2026-07-27T15:40:00+09:00", "SUCCEEDED",
				"2026-07-27T15:52:00+09:00");
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id,
				       etf_instrument_id, trade_date, explanation_as_of, explanation_type, summary)
				VALUES ('res-w1', 'run-w1', 'etf-w602', '2026-07-27',
				        '2026-07-27T15:40:00+09:00'::timestamptz, 'EVENT_SUPPORTED', '원본 설명 본문.')
				""");
		// run-w2: 결과 행 없는 런 (정정 대상 없음)
		seedRunChain("2", "2026-07-28", "2026-07-28T15:40:00+09:00", "RUNNING", null);
		// run-w3: 게시(PUBLISHED)된 런 — 무효화 대상. 테넌트 2 + 기존 NEW cursor 로
		// 발번 단조성(MAX+1)을 검증한다.
		seedRunChain("3", "2026-07-29", "2026-07-29T15:40:00+09:00", "SUCCEEDED",
				"2026-07-29T15:52:00+09:00");
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id,
				       etf_instrument_id, trade_date, explanation_as_of, explanation_type, summary,
				       publication_status)
				VALUES ('res-w3', 'run-w3', 'etf-w602', '2026-07-29',
				        '2026-07-29T15:40:00+09:00'::timestamptz, 'EVENT_SUPPORTED', '게시된 설명.',
				        'PUBLISHED')
				""");
		// 증권사C 는 게시 후 온보딩된 테넌트 — res-w3 의 NEW 를 받은 적이 없어 무효화
		// 발번 대상에서 제외돼야 한다(원본 없는 INVALIDATION = 가짜 gap 신호 방지).
		// 단 C 에도 **다른 결과**(res-w1)의 NEW 는 시드한다 — 발번 제한이 "아무 NEW"가
		// 아니라 "그 결과의 NEW" 상관 조건임을 테스트가 실제로 거부할 수 있게(Rule 9).
		jdbc.update("""
				INSERT INTO tenant (tenant_name, environment, status)
				VALUES ('증권사A', 'DEV', 'ACTIVE'), ('증권사B', 'DEV', 'ACTIVE'),
				       ('증권사C', 'DEV', 'ONBOARDING')
				""");
		jdbc.update("""
				INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, explanation_result_id)
				SELECT tenant_id, 1, 'NEW', 'res-w1' FROM tenant WHERE tenant_name = '증권사C'
				""");
		// 테넌트별 cursor 대열이 서로 다르게 시작하도록 기존 NEW 를 비대칭으로 시드
		jdbc.update("""
				INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, explanation_result_id)
				SELECT tenant_id, 1, 'NEW', 'res-w3' FROM tenant WHERE tenant_name = '증권사A'
				""");
		jdbc.update("""
				INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, explanation_result_id)
				SELECT tenant_id, s.c, 'NEW', 'res-w3' FROM tenant, (VALUES (1), (2)) AS s(c)
				 WHERE tenant_name = '증권사B'
				""");
	}

	private void seedRunChain(String n, String tradeDate, String detectedAt, String runStatus,
			String finishedAt) {
		jdbc.update("""
				INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id,
				       trade_date, detected_at, observed_return, absolute_gate_triggered,
				       relative_gate_triggered, detection_policy_version)
				VALUES (?, 'etf-w602', ?::date, ?::timestamptz, -0.0342, true, false, 'p1')
				""", "trg-w" + n, tradeDate, detectedAt);
		jdbc.update("""
				INSERT INTO etf_contribution_observation (contribution_observation_id,
				       price_movement_trigger_id, available_at, data_version)
				VALUES (?, ?, ?::timestamptz, 'd1')
				""", "co-w" + n, "trg-w" + n, detectedAt);
		jdbc.update("""
				INSERT INTO explanation_route (explanation_route_id, contribution_observation_id,
				       route_code, event_search_required, evaluated_at)
				VALUES (?, ?, 'CONCENTRATED', true, ?::timestamptz)
				""", "rt-w" + n, "co-w" + n, detectedAt);
		jdbc.update("""
				INSERT INTO explanation_run (explanation_run_id, explanation_route_id,
				       bundle_version, explanation_as_of, run_status, started_at, finished_at)
				VALUES (?, ?, 'v1', ?::timestamptz, ?, ?::timestamptz, ?::timestamptz)
				""", "run-w" + n, "rt-w" + n, detectedAt, runStatus, detectedAt, finishedAt);
	}

	@Test
	void 정정은_원본을_덮지_않고_변경_전후를_감사에_남긴다() {
		boolean ok = writes.correct("run-w1", "정정된 설명 본문.", "근거 재확인", OPERATOR);

		assertThat(ok).isTrue();
		// explanation_result(파이프라인 소유)는 불변 — super-admin-api 는 reader 다(단일 writer 규약).
		assertThat(jdbc.queryForObject(
				"SELECT summary FROM explanation_result WHERE explanation_run_id = 'run-w1'",
				String.class)).isEqualTo("원본 설명 본문.");

		Map<String, Object> log = jdbc.queryForMap("""
				SELECT action, actor_email, actor_name, target_type, target_id, reason,
				       details->>'before' AS before, details->>'after' AS after
				  FROM admin_activity_log WHERE target_id = 'run-w1'
				""");
		assertThat(log.get("action")).isEqualTo("ANALYSIS_RESULT_CORRECTED");
		assertThat(log.get("actor_email")).isEqualTo("ops@edge.io");
		assertThat(log.get("actor_name")).isEqualTo("운영자");
		assertThat(log.get("target_type")).isEqualTo("ANALYSIS_RUN");
		assertThat(log.get("reason")).isEqualTo("근거 재확인");
		assertThat(log.get("before")).isEqualTo("원본 설명 본문.");
		assertThat(log.get("after")).isEqualTo("정정된 설명 본문.");
	}

	/** 연속 정정의 before 는 직전 정정본이다 — 감사 체인이 실제 전이(원본→1차→2차)를 재현한다. */
	@Test
	void 연속_정정의_before는_직전_정정본이다() {
		writes.correct("run-w1", "1차 정정.", "r1", OPERATOR);
		writes.correct("run-w1", "2차 정정.", "r2", OPERATOR);

		List<Map<String, Object>> logs = jdbc.queryForList("""
				SELECT details->>'before' AS before, details->>'after' AS after
				  FROM admin_activity_log WHERE target_id = 'run-w1' ORDER BY activity_id
				""");
		assertThat(logs).hasSize(2);
		assertThat(logs.get(0)).containsEntry("before", "원본 설명 본문.").containsEntry("after", "1차 정정.");
		assertThat(logs.get(1)).containsEntry("before", "1차 정정.").containsEntry("after", "2차 정정.");
	}

	@Test
	void 결과_행_없는_런의_정정은_대상이_없어_false다() {
		boolean ok = writes.correct("run-w2", "무의미", "사유", OPERATOR);

		assertThat(ok).isFalse();
		assertThat(jdbc.queryForObject(
				"SELECT count(*) FROM admin_activity_log", Long.class)).isZero();
	}

	@Test
	void 제외는_감사에_남고_없는_런은_false다() {
		assertThat(writes.exclude("run-w1", "근거 신뢰도 미달", OPERATOR)).isTrue();
		assertThat(writes.exclude("nope", "사유", OPERATOR)).isFalse();

		Map<String, Object> log = jdbc.queryForMap(
				"SELECT action, reason, details FROM admin_activity_log WHERE target_id = 'run-w1'");
		assertThat(log.get("action")).isEqualTo("ANALYSIS_EXCLUDED");
		assertThat(log.get("reason")).isEqualTo("근거 신뢰도 미달");
		// 제외/복원은 전후 페이로드가 없다 — details 는 null
		assertThat(log.get("details")).isNull();
		assertThat(jdbc.queryForObject(
				"SELECT count(*) FROM admin_activity_log", Long.class)).isEqualTo(1L);
	}

	@Test
	void 복원은_감사에_남고_사유_없이도_기록된다() {
		assertThat(writes.restore("run-w1", null, OPERATOR)).isTrue();

		Map<String, Object> log = jdbc.queryForMap(
				"SELECT action, reason FROM admin_activity_log WHERE target_id = 'run-w1'");
		assertThat(log.get("action")).isEqualTo("ANALYSIS_RESTORED");
		assertThat(log.get("reason")).isNull();
	}

	/** 쓰기가 읽기 오버레이에 반영된다 — 제외 후 EXCLUDED, 복원 후 원상태, 정정본이 원본을 덮지 않고 오버레이. */
	@Test
	void 쓰기는_읽기_오버레이에_왕복_반영된다() {
		writes.correct("run-w1", "정정 본문", "근거 재확인", OPERATOR);
		writes.exclude("run-w1", "근거 미달", OPERATOR);
		assertThat(rowOf("run-w1").excluded()).isTrue();
		assertThat(rowOf("run-w1").corrected()).isTrue();
		// 원장 원본(summary)은 불변, 정정본은 오버레이(correctedSummary)로 온다
		assertThat(rowOf("run-w1").summary()).isEqualTo("원본 설명 본문.");
		assertThat(rowOf("run-w1").correctedSummary()).isEqualTo("정정 본문");

		writes.restore("run-w1", null, OPERATOR);
		assertThat(rowOf("run-w1").excluded()).isFalse();
		// 복원해도 정정 이력은 남는다
		assertThat(rowOf("run-w1").corrected()).isTrue();
	}

	private AnalysisRow rowOf(String runId) {
		return reads.list().stream().filter(r -> r.runId().equals(runId)).findFirst().orElseThrow();
	}

	/**
	 * 무효화는 게시본 WITHDRAWN 전이 + 전 테넌트 INVALIDATION 발번 + 감사를 한 트랜잭션으로
	 * 남긴다. cursor 는 테넌트별 MAX+1 — 단조성이 깨지면 sync 소비자가 이벤트를 건너뛴다
	 * (유실 방지 계약, sync-protocol.md).
	 */
	@Test
	void 무효화는_전이_발번_감사를_한_트랜잭션으로_남긴다() {
		var outcome = writes.invalidate("run-w3", "전제 데이터 정정", OPERATOR);

		assertThat(outcome).isEqualTo(
				InvalidateOutcome.INVALIDATED);
		assertThat(jdbc.queryForObject(
				"SELECT publication_status FROM explanation_result WHERE explanation_result_id = 'res-w3'",
				String.class)).isEqualTo("WITHDRAWN");

		// NEW 수신 테넌트에만 INVALIDATION 1행, cursor = 각자의 기존 MAX+1 (A: 1→2, B: 2→3).
		// NEW 를 받은 적 없는 증권사C 에는 발번되지 않는다 — 원본 없는 무효화는 소비측에서
		// 가짜 gap 신호가 된다(sync-protocol.md "원본 미수신 무효화 = gap 에서만").
		List<Map<String, Object>> rows = jdbc.queryForList("""
				SELECT t.tenant_name, d.cursor, d.explanation_result_id,
				       d.target_explanation_result_id, d.reason
				  FROM tenant_delivery d JOIN tenant t ON t.tenant_id = d.tenant_id
				 WHERE d.delivery_type = 'INVALIDATION' ORDER BY t.tenant_name
				""");
		assertThat(rows).hasSize(2);
		assertThat(rows).extracting(r -> r.get("tenant_name")).containsExactly("증권사A", "증권사B");
		assertThat(rows.get(0)).containsEntry("cursor", 2L);
		assertThat(rows.get(1)).containsEntry("cursor", 3L);
		for (Map<String, Object> row : rows) {
			// CHECK(ck_tenant_delivery_payload): 무효화 행은 본체 참조 없이 target·reason 만 싣는다
			assertThat(row.get("explanation_result_id")).isNull();
			assertThat(row.get("target_explanation_result_id")).isEqualTo("res-w3");
			assertThat(row.get("reason")).isEqualTo("전제 데이터 정정");
		}

		Map<String, Object> log = jdbc.queryForMap(
				"SELECT action, reason, actor_email FROM admin_activity_log WHERE target_id = 'run-w3'");
		assertThat(log.get("action")).isEqualTo("ANALYSIS_INVALIDATED");
		assertThat(log.get("reason")).isEqualTo("전제 데이터 정정");
		assertThat(log.get("actor_email")).isEqualTo("ops@edge.io");
	}

	/** 게시본 없는 런(DRAFT)·없는 런은 각각 409/404 신호 — 아무 행도 쓰지 않는다(원자성). */
	@Test
	void 미게시_런과_없는_런의_무효화는_아무_행도_쓰지_않는다() {
		// run-w1 의 res-w1 은 publication_status 기본값 DRAFT
		assertThat(writes.invalidate("run-w1", "사유", OPERATOR)).isEqualTo(
				InvalidateOutcome.NOT_PUBLISHED);
		assertThat(writes.invalidate("nope", "사유", OPERATOR)).isEqualTo(
				InvalidateOutcome.RUN_NOT_FOUND);

		assertThat(jdbc.queryForObject(
				"SELECT count(*) FROM tenant_delivery WHERE delivery_type = 'INVALIDATION'",
				Long.class)).isZero();
		assertThat(jdbc.queryForObject(
				"SELECT count(*) FROM admin_activity_log", Long.class)).isZero();
	}

	/** 재호출은 이미 WITHDRAWN 이라 409 — 중복 발번이 구조적으로 불가능하다(멱등). */
	@Test
	void 무효화_재호출은_중복_발번_없이_409_신호다() {
		writes.invalidate("run-w3", "1차", OPERATOR);
		var second = writes.invalidate("run-w3", "2차", OPERATOR);

		assertThat(second).isEqualTo(
				InvalidateOutcome.NOT_PUBLISHED);
		assertThat(jdbc.queryForObject(
				"SELECT count(*) FROM tenant_delivery WHERE delivery_type = 'INVALIDATION'",
				Long.class)).isEqualTo(2L);
	}

	/**
	 * 무효화 후 같은 (종목, 거래일)에 PUBLISHED 가 없다 — 엔진 day-grain 게이트(EXISTS
	 * PUBLISHED)와 같은 조건이 false 가 되어, 재실행 시 새 게시가 가능해진다(ADR-0045 가
	 * 확정한 "무효화 후 재발번" 해금의 근거).
	 */
	@Test
	void 무효화는_같은_grain의_재게시를_해금한다() {
		writes.invalidate("run-w3", "사유", OPERATOR);

		assertThat(jdbc.queryForObject("""
				SELECT EXISTS (SELECT 1 FROM explanation_result
				 WHERE etf_instrument_id = 'etf-w602' AND trade_date = '2026-07-29'
				   AND publication_status = 'PUBLISHED')
				""", Boolean.class)).isFalse();
	}
}
