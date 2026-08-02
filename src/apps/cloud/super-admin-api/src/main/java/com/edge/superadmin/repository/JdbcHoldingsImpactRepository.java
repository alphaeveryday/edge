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
 * <p>적재 축은 <b>기준일({@code trade_date} = 계약이 해석한 expected_as_of)</b>이다.
 * {@code data_version = run_id} 로 스코프하면 안 된다 — 적재 스텝은 read-merge-overwrite
 * 멱등이라 <b>비중이 안 바뀐 행의 data_version 을 갱신하지 않는다</b>(load_etf_holdings).
 * 재실행 런에서 무변경 행이 이전 run_id 로 남아 정상 ETF 가 거짓 누락이 된다(리뷰 1라운드).
 * 대신 이 판정은 "그 기준일의 적재분이 지금 존재하는가"를 말한다 — 나중 런이 메웠으면
 * 결손이 아닌 것이 맞다.
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

	/** 레인 조건 필수 — 뉴스 런 키를 받으면 "holdings 판정이 없는 런"이 계산 불가로 위장된다. */
	private static final String RUN_BY_KEY_SQL = """
			SELECT pipeline_run_id, run_key FROM ops_pipeline_run
			 WHERE run_key = ? AND pipeline_type = 'etf-daily'
			""";

	private static final String TASK_SQL = """
			SELECT expected_as_of_date, expectation_snapshot_id
			  FROM ops_expected_task
			 WHERE pipeline_run_id = ? AND task_key = '%s'
			""".formatted(HOLDINGS_TASK);

	/**
	 * 진행 중인 holdings 적재가 <b>하나라도</b> 있는가 — loaded/missing 이 기준일 현재 상태
	 * 이므로 진행 판정도 그 상태를 바꿀 수 있는 실행 전체를 본다. 기준일로 좁히지 않는 이유:
	 * 적재 스텝은 창 인자 없이 canonical <b>전 파티션을 스캔</b>하므로 어떤 런의 적재든 이
	 * 기준일을 메울 수 있다(리뷰 3라운드). 미귀결(outcome NULL/PENDING)뿐 아니라 <b>도는
	 * 재시도</b>(귀결 후 RUNNING attempt — outcome 은 완료 시에만 갱신)도 잡는다. 한계: 강제
	 * 종료로 남은 죽은 RUNNING 잔재는 유보를 과대하게 만들 수 있다 — 보수적 방향이고, 그
	 * 잔재는 Reconciler 의 STALLED 이슈가 드러낸다(드릴다운 소관).
	 *
	 * <p>PENDING 의 "진행 중" 판정 축은 <b>런의 생사</b>다 — 작업별 deadline 은 안 된다(선행이
	 * 도는 동안 LOAD 의 짧은 마감이 먼저 지나는 정상 구간을 잔재로 오판 — 집중 검증 라운드).
	 * 죽은 런(기동 실패·terminal·hard deadline 경과)의 영구 PENDING 은 잔재로 걸러 전역 영구
	 * 유보를 막고, 살아 있는 런의 PENDING 은 마감이 지났어도 진행 중이다(EXEC-04 BREACHED).
	 */
	private static final String LOAD_PENDING_SQL = """
			SELECT EXISTS (
			    SELECT 1
			      FROM ops_expected_task l
			      JOIN ops_pipeline_run r ON r.pipeline_run_id = l.pipeline_run_id
			     WHERE l.task_key = 'LOAD_ETF_HOLDINGS'
			       AND (((l.task_outcome IS NULL OR l.task_outcome = 'PENDING')
			             AND (r.orchestration_status = 'RUNNING'
			                  OR (r.orchestration_status IS NULL
			                      AND r.launch_status = 'LAUNCHED'
			                      AND r.hard_deadline_at > now())))
			            OR EXISTS (SELECT 1 FROM ops_task_attempt a
			                        WHERE a.expected_task_id = l.expected_task_id
			                          AND a.execution_status = 'RUNNING')))
			""";

	private static final String SNAPSHOT_IDS_SQL = """
			SELECT jsonb_array_elements_text(entity_ids) AS our_etf_id
			  FROM ops_expectation_snapshot
			 WHERE expectation_snapshot_id = ?
			 ORDER BY 1
			""";

	/**
	 * 기준일에 적재분이 존재하는 ETF 의 <b>단축코드</b> 집합 — 기대 목록과 같은 축으로 돌린다.
	 * instrument.ticker 가 etf_map 의 키(our_etf_id)와 같은 축임은 load_instruments 가 보장한다.
	 * market/type 조건 필수 — ticker 유일성은 시장 안에서만이라, 타시장 동명 ticker 가
	 * loaded 에 섞이면 실제 XKRX 결손이 숨는다(리뷰 1라운드).
	 */
	private static final String LOADED_SQL = """
			SELECT DISTINCT i.ticker
			  FROM etf_holding_snapshot h
			  JOIN instrument i ON i.instrument_id = h.etf_instrument_id
			         AND i.market_code = 'XKRX' AND i.instrument_type = 'ETF'
			 WHERE h.trade_date = ?
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
		LocalDate asOf = task == null ? null : task.expectedAsOf();
		// 기대 목록이 없거나 기준일이 없으면 결손을 계산할 수 없다 — 기준일 없는 채 진행하면
		// 적재·분석 조인이 조용히 공집합이 돼 "전부 누락·분석 없음"으로 오독된다(리뷰 1라운드).
		boolean undetermined = expected.isEmpty() || asOf == null;
		if (undetermined) {
			// 계산 불가 경로의 loadedCount 는 0 이 아니라 null — "0종 적재"와 "모름"을 섞지 않는다.
			return new Impact(run.runKey(), asOf, null, null, true, false, List.of());
		}

		boolean loadPending = Boolean.TRUE.equals(
				jdbc.queryForObject(LOAD_PENDING_SQL, Boolean.class));
		Set<String> loaded = new LinkedHashSet<>(
				jdbc.queryForList(LOADED_SQL, String.class, asOf));

		List<MissingEtf> missing = new ArrayList<>();
		int loadedExpected = 0;   // 기대 ∩ 적재 — 전체 적재 수를 내면 분모(기대)를 넘을 수 있다
		for (String ourEtfId : expected) {
			if (loaded.contains(ourEtfId)) {
				loadedExpected++;
				continue;
			}
			missing.add(missingDetail(ourEtfId, asOf));
		}
		return new Impact(run.runKey(), asOf, expected.size(), loadedExpected, false,
				loadPending, List.copyOf(missing));
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
