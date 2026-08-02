package com.edge.superadmin;

import com.edge.superadmin.repository.HoldingsImpactRepository;
import com.edge.superadmin.repository.HoldingsImpactRepository.Impact;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * holdings 결손 영향 SQL 통합 테스트(ALPHA-686) — 기대(snapshot entity_ids) ↔ 적재
 * (etf_holding_snapshot, data_version=run_id) 차집합과 instrument·분석 조인을 실 스키마에서
 * 잠근다. 핵심 등식: {@code data_version = pipeline_run_id}(적재 스텝이 run_id 를 그대로 넣음).
 */
@Transactional
class JdbcHoldingsImpactRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	private static final String RUN_ID = "run-impact";
	private static final String RUN_KEY = "etf-daily:2026-07-31T15:40";
	private static final LocalDate AS_OF = LocalDate.of(2026, 7, 31);

	@Autowired
	private HoldingsImpactRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	@BeforeEach
	void fixture() {
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, orchestration_status, trading_date, created_at)
				VALUES (?, ?, 'etf-daily', 'exec-i', 'LAUNCHED', 'SUCCEEDED',
				        ?::date, '2026-07-31T06:40:00Z'::timestamptz)
				""", RUN_ID, RUN_KEY, AS_OF);
		jdbc.update("""
				INSERT INTO ops_expectation_snapshot (expectation_snapshot_id, pipeline_run_id,
				       task_key, entity_kind, expected_entity_count, entity_ids)
				VALUES ('snap-i', ?, 'ETF_HOLDINGS_COLLECTION_KRX', 'ticker', 3,
				        '["999001","999002","9ZZA00"]'::jsonb)
				""", RUN_ID);
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, data_status, idempotency_key,
				       expected_as_of_date, expectation_snapshot_id)
				VALUES ('et-i', ?, 'ETF_HOLDINGS_COLLECTION_KRX', 'raw', 'etf_holdings',
				        'DUE', 'FULFILLED', 'INCOMPLETE', 'et-i-key', ?::date, 'snap-i')
				""", RUN_ID, AS_OF);

		// ETF 2종은 instrument 존재: 999001(적재 성공) · 999002(적재 누락, 당일 분석 있음).
		// 9ZZA00 은 instrument 행 자체가 없음(프로필까지 결손) — 단축코드 경로.
		// 티커는 합성값 — 실코드(069500 등)는 시드 데이터와 unique 충돌한다.
		insertEtf("inst-ok", "999001", "KODEX 200");
		insertEtf("inst-miss", "999002", "KODEX 반도체");
		insertConstituent("inst-c", "999930");
		jdbc.update("""
				INSERT INTO etf_holding_snapshot (etf_instrument_id, constituent_instrument_id,
				       trade_date, weight_ratio, available_at, data_version)
				VALUES ('inst-ok', 'inst-c', ?::date, 0.5,
				        '2026-07-31T06:45:00Z'::timestamptz, ?)
				""", AS_OF, RUN_ID);
		// 다른 런의 적재는 이 런의 "적재됨"이 아니다 — data_version 스코프를 잠근다.
		jdbc.update("""
				INSERT INTO etf_holding_snapshot (etf_instrument_id, constituent_instrument_id,
				       trade_date, weight_ratio, available_at, data_version)
				VALUES ('inst-miss', 'inst-c', ?::date, 0.5,
				        '2026-07-30T06:45:00Z'::timestamptz, 'run-earlier')
				""", AS_OF);

		insertAnalysis("inst-miss");
	}

	@Test
	void 기대와_이_런의_적재_차집합이_누락이고_분석과_이름이_붙는다() {
		Impact impact = repository.impact(RUN_KEY);

		assertThat(impact.expectedCount()).isEqualTo(3);
		assertThat(impact.loadedCount()).isEqualTo(1);   // inst-ok 만 — 다른 런 적재는 안 센다
		assertThat(impact.snapshotMissing()).isFalse();
		assertThat(impact.expectedAsOf()).isEqualTo(AS_OF);
		assertThat(impact.missing()).hasSize(2);

		HoldingsImpactRepository.MissingEtf withAnalysis = impact.missing().stream()
				.filter(m -> m.ourEtfId().equals("999002")).findFirst().orElseThrow();
		assertThat(withAnalysis.instrumentId()).isEqualTo("inst-miss");
		assertThat(withAnalysis.etfName()).isEqualTo("KODEX 반도체");
		assertThat(withAnalysis.analyses()).singleElement()
				.satisfies(a -> assertThat(a.explanationResultId()).isEqualTo("res-i"));

		// instrument 행 부재 ETF 도 단축코드로 내려간다 — 화면에서 사라지면 안 된다.
		HoldingsImpactRepository.MissingEtf codeOnly = impact.missing().stream()
				.filter(m -> m.ourEtfId().equals("9ZZA00")).findFirst().orElseThrow();
		assertThat(codeOnly.instrumentId()).isNull();
		assertThat(codeOnly.analyses()).isEmpty();
	}

	@Test
	void runKey_없으면_최신_슬롯의_런이고_원장_비면_null_이다() {
		Impact latest = repository.impact(null);
		assertThat(latest.runKey()).isEqualTo(RUN_KEY);

		jdbc.update("DELETE FROM ops_expected_task");
		jdbc.update("DELETE FROM ops_expectation_snapshot");
		jdbc.update("DELETE FROM ops_pipeline_run");
		assertThat(repository.impact(null)).isNull();
	}

	@Test
	void 기대_snapshot_이_없으면_계산_불가로_드러난다() {
		jdbc.update("UPDATE ops_expected_task SET expectation_snapshot_id = NULL"
				+ " WHERE expected_task_id = 'et-i'");

		Impact impact = repository.impact(RUN_KEY);

		// WHY: 스펙 §6.3 — 계산 불가(UNKNOWN)를 빈 누락 목록(영향 없음)과 같은 모양으로 내면
		//      미배선 런이 "결손 없음"으로 읽힌다.
		assertThat(impact.snapshotMissing()).isTrue();
		assertThat(impact.expectedCount()).isNull();
		assertThat(impact.missing()).isEmpty();
	}

	private void insertEtf(String instrumentId, String ticker, String name) {
		jdbc.update("INSERT INTO entity (entity_id, entity_type, display_name) VALUES (?, 'INSTRUMENT', ?)",
				instrumentId, name);
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES (?, 'XKRX', ?, 'ETF')
				""", instrumentId, ticker);
		jdbc.update("INSERT INTO etf_profile (instrument_id, etf_type) VALUES (?, 'SECTOR')",
				instrumentId);
	}

	private void insertConstituent(String instrumentId, String ticker) {
		jdbc.update("INSERT INTO entity (entity_id, entity_type, display_name) VALUES (?, 'INSTRUMENT', ?)",
				instrumentId, ticker);
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES (?, 'XKRX', ?, 'EQUITY')
				""", instrumentId, ticker);
	}

	/** 누락 ETF 의 기준일 설명 — trigger→관찰→경로→런→결과 최소 체인. */
	private void insertAnalysis(String etfInstrumentId) {
		jdbc.update("""
				INSERT INTO release_bundle (bundle_version, component_versions, component_hash, status)
				VALUES ('v1', '{"engine":"1"}'::jsonb, ?, 'DRAFT')
				""", "a".repeat(64));
		jdbc.update("""
				INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id,
				       trade_date, detected_at, observed_return, absolute_gate_triggered,
				       relative_gate_triggered, detection_policy_version)
				VALUES ('trg-i', ?, ?::date, '2026-07-31T06:40:00Z'::timestamptz, -0.03, true, false, 'p1')
				""", etfInstrumentId, AS_OF);
		jdbc.update("""
				INSERT INTO etf_contribution_observation (contribution_observation_id,
				       price_movement_trigger_id, available_at, data_version)
				VALUES ('co-i', 'trg-i', '2026-07-31T06:40:00Z'::timestamptz, 'd1')
				""");
		jdbc.update("""
				INSERT INTO explanation_route (explanation_route_id, contribution_observation_id,
				       route_code, event_search_required, evaluated_at)
				VALUES ('rt-i', 'co-i', 'CONCENTRATED', true, '2026-07-31T06:40:00Z'::timestamptz)
				""");
		jdbc.update("""
				INSERT INTO explanation_run (explanation_run_id, explanation_route_id,
				       bundle_version, explanation_as_of, run_status, started_at, finished_at)
				VALUES ('run-a', 'rt-i', 'v1', '2026-07-31T06:40:00Z'::timestamptz, 'SUCCEEDED',
				        '2026-07-31T06:40:00Z'::timestamptz, '2026-07-31T06:52:00Z'::timestamptz)
				""");
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id,
				       etf_instrument_id, trade_date, explanation_as_of, explanation_type,
				       summary, confidence_level)
				VALUES ('res-i', 'run-a', ?, ?::date,
				        '2026-07-31T06:40:00Z'::timestamptz, 'EVENT_SUPPORTED',
				        '반도체 업황 회복 기대', 'HIGH')
				""", etfInstrumentId, AS_OF);
	}
}
