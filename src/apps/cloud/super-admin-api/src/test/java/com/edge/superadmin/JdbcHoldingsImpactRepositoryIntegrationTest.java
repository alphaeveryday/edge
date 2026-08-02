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
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, idempotency_key)
				VALUES ('et-load', ?, 'LOAD_ETF_HOLDINGS', 'feature', 'etf_holdings',
				        'DUE', 'FULFILLED', 'et-load-key')
				""", RUN_ID);

		// ETF 2종은 instrument 존재: 999001(기준일 적재 있음 — 단, data_version 은 **다른
		// run_id**: 멱등 적재는 무변경 행의 버전을 안 갈아서 run_id 스코프는 거짓 누락을 만든다,
		// 리뷰 1라운드 반례) · 999002(기준일 적재 없음 = 진짜 누락, 당일 분석 있음).
		// 9ZZA00 은 instrument 행 자체가 없음(프로필까지 결손) — 단축코드 경로.
		// 티커는 합성값 — 실코드(069500 등)는 시드 데이터와 unique 충돌한다.
		insertEtf("inst-ok", "999001", "KODEX 200");
		insertEtf("inst-miss", "999002", "KODEX 반도체");
		insertConstituent("inst-c", "999930");
		jdbc.update("""
				INSERT INTO etf_holding_snapshot (etf_instrument_id, constituent_instrument_id,
				       trade_date, weight_ratio, available_at, data_version)
				VALUES ('inst-ok', 'inst-c', ?::date, 0.5,
				        '2026-07-30T06:45:00Z'::timestamptz, 'run-earlier')
				""", AS_OF);
		// 다른 기준일의 적재는 이 기준일의 "적재됨"이 아니다 — 시간 축을 잠근다.
		jdbc.update("""
				INSERT INTO etf_holding_snapshot (etf_instrument_id, constituent_instrument_id,
				       trade_date, weight_ratio, available_at, data_version)
				VALUES ('inst-miss', 'inst-c', '2026-07-30'::date, 0.5,
				        '2026-07-30T06:45:00Z'::timestamptz, 'run-earlier')
				""");
		// 타시장 동명 ticker 는 loaded 로 세지 않는다 — 세면 실제 XKRX 결손이 숨는다.
		jdbc.update("INSERT INTO entity (entity_id, entity_type, display_name) VALUES ('inst-kos', 'INSTRUMENT', '동명 KOSDAQ ETF')");
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES ('inst-kos', 'XKOS', '999002', 'ETF')
				""");
		jdbc.update("INSERT INTO etf_profile (instrument_id, etf_type) VALUES ('inst-kos', 'SECTOR')");
		jdbc.update("""
				INSERT INTO etf_holding_snapshot (etf_instrument_id, constituent_instrument_id,
				       trade_date, weight_ratio, available_at, data_version)
				VALUES ('inst-kos', 'inst-c', ?::date, 0.5,
				        '2026-07-31T06:45:00Z'::timestamptz, 'run-kos')
				""", AS_OF);

		insertAnalysis("inst-miss");
	}

	@Test
	void 기대와_기준일_적재의_차집합이_누락이고_분석과_이름이_붙는다() {
		Impact impact = repository.impact(RUN_KEY);

		assertThat(impact.expectedCount()).isEqualTo(3);
		assertThat(impact.loadPending()).isFalse();
		// inst-ok 만 — 다른 run_id 여도 기준일 적재면 센다(멱등 무변경 행 반례),
		// 타시장 동명 ticker(inst-kos)와 다른 기준일 적재(inst-miss 07-30)는 안 센다.
		assertThat(impact.loadedCount()).isEqualTo(1);
		assertThat(impact.snapshotMissing()).isFalse();
		assertThat(impact.expectedAsOf()).isEqualTo(AS_OF);
		assertThat(impact.missing()).hasSize(2);

		HoldingsImpactRepository.MissingEtf withAnalysis = impact.missing().stream()
				.filter(m -> m.ourEtfId().equals("999002")).findFirst().orElseThrow();
		assertThat(withAnalysis.instrumentId()).isEqualTo("inst-miss");
		assertThat(withAnalysis.etfName()).isEqualTo("KODEX 반도체");
		assertThat(withAnalysis.analyses()).singleElement().satisfies(a -> {
			assertThat(a.explanationResultId()).isEqualTo("res-i");
			// 분석 상세 링크 키 — 실 뷰(explanation_result_latest)가 run id 를 내리는지 잠근다
			assertThat(a.explanationRunId()).isEqualTo("run-a");
		});

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
		// 계산 불가의 적재 수는 0 이 아니라 null — "0종 적재"와 "모름"을 섞지 않는다(리뷰 2라운드).
		assertThat(impact.loadedCount()).isNull();
		assertThat(impact.missing()).isEmpty();
	}

	@Test
	void 적재가_돌고_있으면_판정이_유보된다() {
		// WHY: loaded/missing 은 기준일 현재 상태다 — 적재 스텝은 창 인자 없이 전 파티션을
		//      스캔하므로 **어떤 런의 적재든** 이 기준일을 메울 수 있다(리뷰 2·3라운드).
		//      선택 런의 귀결만 보면 지금 메워지는 중인 결손에 수동 복구를 권고한다.
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, orchestration_status, trading_date, created_at)
				VALUES ('run-b', 'etf-daily:2026-07-31T18:00', 'etf-daily', 'exec-b',
				        'LAUNCHED', 'RUNNING', ?::date, '2026-07-31T09:00:00Z'::timestamptz)
				""", AS_OF);
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, idempotency_key, expected_as_of_date)
				VALUES ('et-b-h', 'run-b', 'ETF_HOLDINGS_COLLECTION_KRX', 'raw', 'etf_holdings',
				        'DUE', 'FULFILLED', 'et-b-h-key', ?::date)
				""", AS_OF);
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, idempotency_key, deadline_at)
				VALUES ('et-b-l', 'run-b', 'LOAD_ETF_HOLDINGS', 'feature', 'etf_holdings',
				        'DUE', 'PENDING', 'et-b-l-key', now() - interval '1 hour')
				""");
		// deadline 은 일부러 과거 — 살아 있는 런(RUNNING)의 마감 경과 PENDING 은 여전히
		// 진행 중이다(선행이 도는 동안 LOAD 짧은 마감이 먼저 지나는 정상 구간, EXEC-04).

		Impact impact = repository.impact(RUN_KEY);   // A 런을 조회해도

		assertThat(impact.loadPending()).isTrue();    // 다른 런의 미귀결 적재가 유보를 만든다
	}

	@Test
	void 귀결_후_도는_재시도도_유보로_잡힌다() {
		// WHY: 재시도는 RUNNING attempt 만 추가하고 task_outcome 은 완료 시에만 갱신된다 —
		//      outcome 만 보면 실제 적재 진행 중에 중복 수동 복구를 권고한다(리뷰 3라운드).
		jdbc.update("""
				INSERT INTO ops_task_attempt (attempt_id, expected_task_id, ecs_task_arn,
				       execution_status, started_at)
				VALUES ('att-retry', 'et-load', 'arn:task/retry', 'RUNNING',
				        '2026-07-31T08:00:00Z'::timestamptz)
				""");

		assertThat(repository.impact(RUN_KEY).loadPending()).isTrue();
	}

	@Test
	void 죽은_런의_영구_PENDING_잔재는_유보를_만들지_않는다() {
		// WHY: 기동 실패 런의 LOAD 행은 영원히 PENDING 이다(Reconciler 미귀결) — 그 잔재
		//      하나가 전역 유보를 영구화하면 실제 결손·복구 안내가 계속 숨는다(검증 라운드).
		//      판정 축은 런의 생사다 — LAUNCH_FAILED 는 orchestration 이 영영 null 이라
		//      launch 축으로 걸러진다.
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, created_at)
				VALUES ('run-dead', 'etf-daily:2026-07-01T15:40', 'etf-daily', 'exec-d',
				        'LAUNCH_FAILED', '2026-07-01T06:40:00Z'::timestamptz)
				""");
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, idempotency_key, deadline_at)
				VALUES ('et-dead-l', 'run-dead', 'LOAD_ETF_HOLDINGS', 'feature', 'etf_holdings',
				        'DUE', 'PENDING', 'et-dead-key', '2026-07-01T09:00:00Z'::timestamptz)
				""");

		assertThat(repository.impact(RUN_KEY).loadPending()).isFalse();
	}

	@Test
	void 다른_레인의_runKey_는_holdings_판정으로_수락되지_않는다() {
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, created_at)
				VALUES ('run-news', 'news:2026-07-31T15:30', 'news', 'exec-n', 'LAUNCHED',
				        '2026-07-31T06:30:00Z'::timestamptz)
				""");

		// WHY: 뉴스 런 키가 200/계산불가로 내려가면 "holdings 판정이 없는 런"이 UNKNOWN 으로
		//      위장된다(리뷰 2라운드) — 레인 밖 키는 런 미존재와 같은 null(→서비스 404)이다.
		assertThat(repository.impact("news:2026-07-31T15:30")).isNull();
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
