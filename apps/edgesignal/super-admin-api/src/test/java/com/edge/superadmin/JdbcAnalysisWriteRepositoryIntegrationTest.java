package com.edge.superadmin;

import com.edge.superadmin.auth.SessionOperator;
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
 * 쓰기 원장 통합 테스트 — 무효화 단독(ALPHA-440, 구 3종 오버레이는 ALPHA-737 은퇴). 실
 * explanation_* + tenant_delivery + admin_activity_log 스키마(Testcontainers + Flyway
 * migrations-cloud)를 대상으로 전이·발번·감사·cursor 단조성·원자성·멱등을 검증한다. 손
 * 페이크만으로는 발번 SQL 을 한 줄도 실행하지 않는다(Rule 9).
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

	/**
	 * 무효화는 게시본 WITHDRAWN 전이 + NEW 수신 테넌트 INVALIDATION 발번 + 감사를 한 트랜잭션으로
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
