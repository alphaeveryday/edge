package com.edge.superadmin.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * {@link HoldingsImpactRepository} 의 JdbcTemplate 구현(ALPHA-686).
 *
 * <p>런 스코프 키는 {@code etf_holding_snapshot.data_version} 이다 — 적재 스텝이 파이프라인
 * {@code run_id}(= {@code ops_pipeline_run.pipeline_run_id})를 그대로 넣는다(load_etf_holdings).
 * 이 등식 덕에 RDS 만으로 "이 런이 적재한 ETF 집합"이 성립한다.
 *
 * <p>전 조회가 한 REPEATABLE READ 스냅샷 안이다 — 기대·적재·분석을 따로 읽으면 그 사이
 * 적재가 커밋돼 "기대 33 적재 33 인데 누락 2" 같은 존재한 적 없는 조합이 조립된다.
 */
@Repository
public class JdbcHoldingsImpactRepository implements HoldingsImpactRepository {

	private static final String HOLDINGS_TASK = "ETF_HOLDINGS_COLLECTION_KRX";

	/** "최신"은 슬롯 시각(run_key) 기준 — created_at 은 백필에 뚫린다(Overview 와 같은 이유). */
	private static final String LATEST_MARKET_RUN_SQL = """
			SELECT pipeline_run_id, run_key FROM ops_pipeline_run
			 WHERE pipeline_type = 'etf-daily'
			 ORDER BY run_key DESC, pipeline_run_id DESC
			 LIMIT 1
			""";

	private static final String RUN_BY_KEY_SQL = """
			SELECT pipeline_run_id, run_key FROM ops_pipeline_run WHERE run_key = ?
			""";

	private static final String TASK_SQL = """
			SELECT expected_as_of_date, expectation_snapshot_id
			  FROM ops_expected_task
			 WHERE pipeline_run_id = ? AND task_key = '%s'
			""".formatted(HOLDINGS_TASK);

	private static final String SNAPSHOT_IDS_SQL = """
			SELECT jsonb_array_elements_text(entity_ids) AS our_etf_id
			  FROM ops_expectation_snapshot
			 WHERE expectation_snapshot_id = ?
			 ORDER BY 1
			""";

	/**
	 * 이 런이 적재한 ETF 의 <b>단축코드</b> 집합 — 기대 목록과 같은 축으로 돌린다.
	 * instrument.ticker 가 etf_map 의 키(our_etf_id)와 같은 축임은 load_instruments 가 보장한다.
	 */
	private static final String LOADED_SQL = """
			SELECT DISTINCT i.ticker
			  FROM etf_holding_snapshot h
			  JOIN instrument i ON i.instrument_id = h.etf_instrument_id
			 WHERE h.data_version = ?
			""";

	/**
	 * 누락 ETF 하나의 식별·이름·기준일 분석. LEFT JOIN — instrument 행이 없는 ETF(프로필
	 * 수집까지 결손)도 단축코드로는 내려가야 한다. 동명 KOSDAQ 종목 오매칭을 막기 위해
	 * market/type 을 함께 건다(uq_instrument_market_ticker 는 시장 안에서만 유일).
	 */
	private static final String MISSING_DETAIL_SQL = """
			SELECT i.instrument_id, ent.display_name,
			       r.explanation_result_id, r.publication_status, r.summary
			  FROM (SELECT ?::text AS our_etf_id) m
			  LEFT JOIN instrument i ON i.ticker = m.our_etf_id
			         AND i.market_code = 'XKRX' AND i.instrument_type = 'ETF'
			  LEFT JOIN entity ent ON ent.entity_id = i.instrument_id
			  LEFT JOIN explanation_result_latest r ON r.etf_instrument_id = i.instrument_id
			         AND r.trade_date = ?
			 ORDER BY r.explanation_result_id
			""";

	private final JdbcTemplate jdbc;

	public JdbcHoldingsImpactRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public Impact impact(String runKey) {
		List<RunRow> runs = runKey == null
				? jdbc.query(LATEST_MARKET_RUN_SQL, JdbcHoldingsImpactRepository::mapRun)
				: jdbc.query(RUN_BY_KEY_SQL, JdbcHoldingsImpactRepository::mapRun, runKey);
		if (runs.isEmpty()) {
			return null;
		}
		RunRow run = runs.get(0);

		List<TaskRow> tasks = jdbc.query(TASK_SQL, JdbcHoldingsImpactRepository::mapTask,
				run.pipelineRunId());
		TaskRow task = tasks.isEmpty() ? null : tasks.get(0);

		List<String> expected = (task == null || task.snapshotId() == null)
				? List.of()
				: jdbc.queryForList(SNAPSHOT_IDS_SQL, String.class, task.snapshotId());
		boolean snapshotMissing = expected.isEmpty();

		Set<String> loaded = new LinkedHashSet<>(
				jdbc.queryForList(LOADED_SQL, String.class, run.pipelineRunId()));

		List<MissingEtf> missing = new ArrayList<>();
		LocalDate asOf = task == null ? null : task.expectedAsOf();
		for (String ourEtfId : expected) {
			if (loaded.contains(ourEtfId)) {
				continue;
			}
			missing.add(missingDetail(ourEtfId, asOf));
		}
		return new Impact(run.runKey(), asOf,
				snapshotMissing ? null : expected.size(), loaded.size(), snapshotMissing,
				List.copyOf(missing));
	}

	private MissingEtf missingDetail(String ourEtfId, LocalDate asOf) {
		List<MissingEtf> rows = jdbc.query(MISSING_DETAIL_SQL, rs -> {
			String instrumentId = null;
			String name = null;
			List<AffectedAnalysis> analyses = new ArrayList<>();
			while (rs.next()) {
				instrumentId = rs.getString("instrument_id");
				name = rs.getString("display_name");
				if (rs.getString("explanation_result_id") != null) {
					analyses.add(new AffectedAnalysis(
							rs.getString("explanation_result_id"),
							rs.getString("publication_status"),
							rs.getString("summary")));
				}
			}
			return List.of(new MissingEtf(ourEtfId, instrumentId, name, List.copyOf(analyses)));
		}, ourEtfId, asOf);
		return rows.get(0);
	}

	private record RunRow(String pipelineRunId, String runKey) {
	}

	private record TaskRow(LocalDate expectedAsOf, String snapshotId) {
	}

	private static RunRow mapRun(ResultSet rs, int rowNum) throws SQLException {
		return new RunRow(rs.getString("pipeline_run_id"), rs.getString("run_key"));
	}

	private static TaskRow mapTask(ResultSet rs, int rowNum) throws SQLException {
		java.sql.Date asOf = rs.getDate("expected_as_of_date");
		return new TaskRow(asOf == null ? null : asOf.toLocalDate(),
				rs.getString("expectation_snapshot_id"));
	}
}
