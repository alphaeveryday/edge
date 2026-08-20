package com.edge.superadmin;

import com.edge.superadmin.repository.ConsoleFactsRepository;
import com.edge.superadmin.repository.ConsoleFactsRepository.IntradayAnalysisPoint;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.tuple;

/** 장중 분석 추이의 연속 날짜·코호트 도달·최신 실행 상태를 실 cloud 스키마로 검증한다. */
@Transactional
class JdbcIntradayAnalysisTrendRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	@Autowired
	private ConsoleFactsRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	private long triggerSeq;

	private void insertTrigger(String id, String day, String entity, String kind, String time) {
		jdbc.update("""
				INSERT INTO minute_ingestion_session (session_id, dataset, source_group,
				       session_date, universe_version, universe_hash, expected_window_count)
				VALUES (?, 'price_minute', 'toss', ?::date, 'u1', 'h1', 391)
				ON CONFLICT (dataset, source_group, session_date) DO NOTHING
				""", "session-" + day, day);
		jdbc.update("""
				INSERT INTO minute_price_trigger (trigger_id, entity_id, session_id, window_start,
				       generation, detection_policy_version, open_price, close_price, change_rate,
				       threshold, cooldown_bucket, trigger_kind)
				VALUES (?,?,?,?::timestamptz,1,'v2',1000,1030,0.03,0.03,?,?)
				""", id, entity, "session-" + day, day + "T" + time + "+09:00", triggerSeq++, kind);
	}

	private void insertObservation(String id, String triggerId, String at) {
		jdbc.update("""
				INSERT INTO etf_contribution_observation (contribution_observation_id,
				       minute_price_trigger_id, available_at, data_version)
				VALUES (?,?,?::timestamptz,'d1')
				""", id, triggerId, at);
	}

	private void insertRoute(String id, String observationId, String at) {
		jdbc.update("""
				INSERT INTO explanation_route (explanation_route_id, contribution_observation_id,
				       route_code, event_search_required, evaluated_at)
				VALUES (?,?,'PRICE_ONLY',false,?::timestamptz)
				""", id, observationId, at);
	}

	private void insertRun(String id, String routeId, String status, String at) {
		insertRun(id, routeId, status, at, at);
	}

	private void insertRun(String id, String routeId, String status, String explanationAsOf,
			String startedAt) {
		jdbc.update("""
				INSERT INTO release_bundle (bundle_version, component_versions, component_hash, status)
				VALUES ('v1', '{"engine":"1"}'::jsonb, ?, 'DRAFT')
				ON CONFLICT (bundle_version) DO NOTHING
				""", "a".repeat(64));
		jdbc.update("""
				INSERT INTO explanation_run (explanation_run_id, explanation_route_id,
				       bundle_version, explanation_as_of, run_status, started_at, finished_at)
				VALUES (?,?,'v1',?::timestamptz,?,?::timestamptz,
				        CASE WHEN ? IN ('PENDING','RUNNING') THEN NULL ELSE ?::timestamptz END)
				""", id, routeId, explanationAsOf, status, startedAt, status, startedAt);
	}

	private void insertResult(String id, String runId, String etf, String day, String at,
			String publicationStatus) {
		jdbc.update("""
				INSERT INTO entity (entity_id, entity_type, display_name)
				VALUES (?, 'INSTRUMENT', ?) ON CONFLICT (entity_id) DO NOTHING
				""", etf, etf);
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES (?, 'XKRX', ?, 'ETF') ON CONFLICT (instrument_id) DO NOTHING
				""", etf, etf.toUpperCase());
		jdbc.update("""
				INSERT INTO etf_profile (instrument_id, etf_type)
				VALUES (?, 'SECTOR') ON CONFLICT (instrument_id) DO NOTHING
				""", etf);
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id,
				       etf_instrument_id, trade_date, explanation_as_of, explanation_type,
				       summary, publication_status)
				VALUES (?,?,?,?::date,?::timestamptz,'PRICE_ONLY','요약',?)
				""", id, runId, etf, day, at, publicationStatus);
	}

	@Test
	void 빈_날도_0으로_내고_FIRE_코호트의_도달만_오래된_날부터_센다() {
		insertTrigger("fire-published", "2026-08-02", "ETF-A", "FIRE", "09:31:00");
		insertObservation("obs-published", "fire-published", "2026-08-02T09:32:00+09:00");
		insertRoute("route-published", "obs-published", "2026-08-02T09:33:00+09:00");
		insertRun("run-published", "route-published", "SUCCEEDED", "2026-08-02T09:34:00+09:00");
		insertResult("result-published", "run-published", "etf-a", "2026-08-02",
				"2026-08-02T09:35:00+09:00", "PUBLISHED");

		insertTrigger("fire-active", "2026-08-02", "ETF-B", "FIRE", "10:31:00");
		insertObservation("obs-active", "fire-active", "2026-08-02T10:32:00+09:00");
		insertRoute("route-active", "obs-active", "2026-08-02T10:33:00+09:00");
		insertRun("run-active", "route-active", "RUNNING", "2026-08-02T10:34:00+09:00");

		insertTrigger("fire-unobserved", "2026-08-02", "ETF-C", "FIRE", "11:31:00");
		insertTrigger("revert", "2026-08-02", "ETF-D", "REVERT", "12:31:00");
		/* 00:05 KST는 UTC 전날이다 — 존 변환이 빠지면 08-01로 새어 날짜별 수가 바뀐다. */
		insertTrigger("kst-boundary", "2026-08-02", "ETF-K", "FIRE", "00:05:00");
		insertTrigger("next-day", "2026-08-03", "ETF-E", "FIRE", "09:31:00");
		insertObservation("obs-next", "next-day", "2026-08-03T09:32:00+09:00");

		OffsetDateTime dbNow = jdbc.queryForObject("SELECT now()", OffsetDateTime.class);
		var trend = repository.intradayAnalysisTrend(LocalDate.parse("2026-08-03"), 3);

		/* 같은 트랜잭션의 now()는 고정이다 — 원장 시각이나 JVM 시각을 대신 내면 깨진다. */
		assertThat(trend.asOf()).isEqualTo(dbNow);
		assertThat(trend.points())
				.extracting(IntradayAnalysisPoint::date, IntradayAnalysisPoint::triggers,
						IntradayAnalysisPoint::observations, IntradayAnalysisPoint::runs,
						IntradayAnalysisPoint::activeRuns, IntradayAnalysisPoint::failedRuns,
						IntradayAnalysisPoint::results, IntradayAnalysisPoint::published)
				.containsExactly(
						tuple(LocalDate.parse("2026-08-01"), 0L, 0L, 0L, 0L, 0L, 0L, 0L),
						tuple(LocalDate.parse("2026-08-02"), 4L, 2L, 2L, 1L, 0L, 1L, 1L),
						tuple(LocalDate.parse("2026-08-03"), 1L, 1L, 0L, 0L, 0L, 0L, 0L));
	}

	@Test
	void 재실행은_도달을_부풀리지_않고_최신_상태만_실패로_분류한다() {
		insertTrigger("fire-rerun", "2026-08-02", "ETF-A", "FIRE", "09:31:00");
		insertObservation("obs-rerun", "fire-rerun", "2026-08-02T09:32:00+09:00");
		insertRoute("route-rerun", "obs-rerun", "2026-08-02T09:33:00+09:00");
		insertRun("run-first", "route-rerun", "SUCCEEDED", "2026-08-02T09:34:00+09:00");
		insertResult("result-first", "run-first", "etf-a", "2026-08-02",
				"2026-08-02T09:35:00+09:00", "PUBLISHED");
		/* 프로듀서가 실제로 만드는 재실행 형상: 기존 게시본은 남고 새 결과는 DRAFT다. */
		insertRun("run-second", "route-rerun", "SUCCEEDED", "2026-08-02T10:34:00+09:00");
		insertResult("result-second", "run-second", "etf-a", "2026-08-02",
				"2026-08-02T10:35:00+09:00", "DRAFT");
		/* explanation_as_of가 started_at보다 먼저, 둘이 같으면 ID가 최신 실행을 결정한다. */
		insertRun("run-started-later", "route-rerun", "RUNNING",
				"2026-08-02T11:00:00+09:00", "2026-08-02T12:00:00+09:00");
		insertRun("run-a-same-time", "route-rerun", "RUNNING",
				"2026-08-02T12:00:00+09:00", "2026-08-02T11:00:00+09:00");
		/* started_at이 먼저다 — 이 행은 ID가 더 커도 최신이 아니어야 한다. */
		insertRun("run-zz-started-earlier", "route-rerun", "PENDING",
				"2026-08-02T12:00:00+09:00", "2026-08-02T10:59:00+09:00");
		insertRun("run-z-latest", "route-rerun", "FAILED",
				"2026-08-02T12:00:00+09:00", "2026-08-02T11:00:00+09:00");

		var point = repository.intradayAnalysisTrend(LocalDate.parse("2026-08-02"), 1).points();
		assertThat(point)
				.singleElement()
				.extracting(IntradayAnalysisPoint::triggers, IntradayAnalysisPoint::observations,
						IntradayAnalysisPoint::runs, IntradayAnalysisPoint::activeRuns,
						IntradayAnalysisPoint::failedRuns, IntradayAnalysisPoint::results,
						IntradayAnalysisPoint::published)
				.containsExactly(1L, 1L, 1L, 0L, 1L, 1L, 1L);

		/* 현재 게시 상태다 — 과거 게시 이력을 합성하면 이 전이 뒤에도 1로 남는다. */
		jdbc.update("UPDATE explanation_result SET publication_status='WITHDRAWN'"
				+ " WHERE explanation_result_id='result-first'");
		assertThat(repository.intradayAnalysisTrend(LocalDate.parse("2026-08-02"), 1).points())
				.singleElement().extracting(IntradayAnalysisPoint::published).isEqualTo(0L);
	}

	@Test
	void PENDING은_active이고_CANCELLED는_runs에만_포함된다() {
		insertTrigger("fire-pending", "2026-08-02", "ETF-P", "FIRE", "09:31:00");
		insertObservation("obs-pending", "fire-pending", "2026-08-02T09:32:00+09:00");
		insertRoute("route-pending", "obs-pending", "2026-08-02T09:33:00+09:00");
		insertRun("run-pending", "route-pending", "PENDING", "2026-08-02T09:34:00+09:00");

		insertTrigger("fire-cancelled", "2026-08-02", "ETF-C", "FIRE", "10:31:00");
		insertObservation("obs-cancelled", "fire-cancelled", "2026-08-02T10:32:00+09:00");
		insertRoute("route-cancelled", "obs-cancelled", "2026-08-02T10:33:00+09:00");
		insertRun("run-cancelled", "route-cancelled", "CANCELLED",
				"2026-08-02T10:34:00+09:00");

		assertThat(repository.intradayAnalysisTrend(LocalDate.parse("2026-08-02"), 1).points())
				.singleElement()
				.extracting(IntradayAnalysisPoint::triggers, IntradayAnalysisPoint::observations,
						IntradayAnalysisPoint::runs, IntradayAnalysisPoint::activeRuns,
						IntradayAnalysisPoint::failedRuns, IntradayAnalysisPoint::results)
				.containsExactly(2L, 2L, 2L, 1L, 0L, 0L);
	}
}
