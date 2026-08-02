package com.edge.superadmin.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * {@link PipelineStatusRepository} 의 JdbcTemplate 구현 — 런 하나를 헤더·작업·시도·이슈 네 조회로
 * 읽어 서비스 층에서 조립한다(ALPHA-514, 드릴다운 ALPHA-574).
 *
 * <p><b>조인 하나로 합치지 않는 이유</b>: 시도는 작업당 여러 개, 이슈는 런/작업/슬롯 세 스코프에
 * 걸쳐 있다. 한 SELECT 로 붙이면 25행이 시도 수 × 이슈 수만큼 불어나고, 중복을 코드에서 다시
 * 걷어내야 한다. 런 하나에 25작업 규모라 네 번 왕복이 문제 될 크기가 아니다.
 *
 * <p>이전 구현은 {@code LEFT JOIN LATERAL … LIMIT 1} 로 <b>마지막 시도만</b> 남겼다. 그래서 재시도
 * 이력과 {@code RECONCILER_BACKFILL}(사후 복구) 구분이 화면에 도달하기 전에 사라졌다.
 */
@Repository
public class JdbcPipelineStatusRepository implements PipelineStatusRepository {

	/**
	 * "최신 런"의 정의는 {@code created_at} 내림차순이다 — 슬롯 키(ALPHA-564)가 시각이라 하루에
	 * 여러 런이 있을 수 있고, 그중 마지막으로 <b>계획된</b> 것이 현재 관심 대상이다.
	 *
	 * <p>동률 해소를 명시한다({@code pipeline_run_id}) — {@code created_at} 에 유일성 제약이 없어서
	 * 동률이면 LIMIT 1 이 매 조회마다 다른 행을 고를 수 있다. 새로고침할 때마다 화면이 흔들리면
	 * 운영자는 무엇이 사실인지 못 정한다.
	 */
	private static final String LATEST_RUN_SQL = """
			SELECT pipeline_run_id, run_key, launch_status, orchestration_status, trading_date
			  FROM ops_pipeline_run
			 ORDER BY created_at DESC, pipeline_run_id DESC
			 LIMIT 1
			""";

	/** {@code run_key} 에 UNIQUE 제약이 있어 정렬·LIMIT 없이 최대 한 행이다. */
	private static final String RUN_BY_KEY_SQL = """
			SELECT pipeline_run_id, run_key, launch_status, orchestration_status, trading_date
			  FROM ops_pipeline_run
			 WHERE run_key = ?
			""";

	/**
	 * stage 정렬을 CASE 로 고정하는 이유: 파이프라인 순서는 raw→normalize→feature 인데 문자열
	 * 정렬은 feature→normalize→raw 라 <b>역순</b>이 된다. 운영자는 앞 단계부터 읽는다.
	 */
	private static final String TASKS_SQL = """
			SELECT expected_task_id, stage, task_key, dataset, plan_status, task_outcome,
			       data_status, records_out, failed_records, expected_at, deadline_at,
			       missed_at, fulfilled_at, skip_reason, outcome_reason, current_attempt_id,
			       completeness IS NOT NULL AS has_completeness,
			       (completeness ->> 'expected')::bigint AS completeness_expected,
			       (completeness ->> 'received')::bigint AS completeness_received,
			       (completeness ->> 'missing')::bigint AS completeness_missing
			  FROM ops_expected_task
			 WHERE pipeline_run_id = ?
			 ORDER BY CASE stage WHEN 'raw' THEN 0 WHEN 'normalize' THEN 1 ELSE 2 END, task_key
			""";

	/**
	 * 시각 <b>오름차순</b> — 이력을 위에서 아래로 읽는 순서다.
	 *
	 * <p>이 순서는 <b>표시 순서일 뿐</b>이고 "현재 시도"의 근거가 아니다({@code current_attempt_id}
	 * 가 그 답이다 — {@code TaskStatus.currentAttempt()}). 사후 복구는 {@code started_at} 에 복구
	 * 시각을 넣으므로 시각 순서가 곧 실행 순서는 아니다.
	 *
	 * <p>{@code NULLS FIRST} 와 {@code attempt_id} 동률 해소가 함께 있어야 순서가 결정적이다.
	 * {@code started_at} 은 스키마가 NULL 을 허용하고 유일성 제약도 없다 — 빼면 목록 순서가
	 * 조회마다 달라져 새로고침할 때마다 이력이 뒤바뀐다.
	 */
	private static final String ATTEMPTS_SQL = """
			SELECT a.expected_task_id, a.attempt_id, a.attempt_number, a.ecs_task_arn,
			       a.execution_status, a.started_at, a.finished_at, a.exit_code,
			       a.failure_reason, a.record_source
			  FROM ops_task_attempt a
			  JOIN ops_expected_task t ON t.expected_task_id = a.expected_task_id
			 WHERE t.pipeline_run_id = ?
			 ORDER BY a.started_at ASC NULLS FIRST, a.attempt_id ASC
			""";

	/**
	 * 이슈의 스코프 3종을 모두 훑는다 — Reconciler 는 {@code scope='run'}→{@code pipeline_run_id},
	 * {@code scope='task'}→{@code expected_task_id}, {@code scope='slot'}→{@code run_key} 로 쓴다
	 * (실코드 확인). 한 스코프라도 빼면 그 종류의 이슈는 화면에서 영영 안 보인다.
	 *
	 * <p>task 스코프는 조인해 {@code task_key} 로 바꾼다 — 내부 ID 는 운영자가 아는 이름이 아니다.
	 *
	 * <p>OPEN 을 먼저 낸다. RESOLVED 도 함께 내리는 이유는 지난 런의 드릴다운에서 "그때 무슨 일이
	 * 있었나"가 해결 여부와 별개의 이력이기 때문이다.
	 */
	private static final String ISSUES_SQL = """
			SELECT i.issue_type, i.scope, i.status, i.occurrence_count, i.first_seen_at,
			       i.last_seen_at, i.resolution_reason, t.task_key
			  FROM ops_reconciliation_issue i
			  LEFT JOIN ops_expected_task t
			         ON i.scope = 'task' AND t.expected_task_id = i.scope_key
			 WHERE (i.scope = 'run'  AND i.scope_key = ?)
			    OR (i.scope = 'slot' AND i.scope_key = ?)
			    OR (i.scope = 'task' AND i.scope_key IN (
			            SELECT expected_task_id FROM ops_expected_task WHERE pipeline_run_id = ?))
			 ORDER BY (i.status = 'OPEN') DESC, i.last_seen_at DESC, i.issue_id
			""";

	/**
	 * LEFT JOIN — 기대 작업이 하나도 안 적힌 런(기동 실패 등)도 슬롯으로 남는다. INNER 로 바꾸면
	 * "아예 못 뜬 슬롯"이 격자에서 통째로 사라진다.
	 *
	 * <p>창의 기준은 {@code created_at}(계획 시각)이다 — 거래일({@code trading_date})은 비거래일
	 * 런에서 NULL 이라 창 기준으로 쓰면 그 런들이 창 밖으로 새어 나간다.
	 */
	private static final String GRID_SQL = """
			SELECT r.pipeline_run_id, r.run_key, r.launch_status, r.orchestration_status,
			       r.trading_date,
			       t.stage, t.task_key, t.plan_status, t.task_outcome, t.data_status,
			       t.records_out, t.failed_records, t.skip_reason, t.outcome_reason,
			       (t.task_outcome = 'PENDING'
			        AND EXISTS (SELECT 1 FROM ops_task_attempt a
			                     WHERE a.expected_task_id = t.expected_task_id
			                       AND a.execution_status = 'RUNNING')) AS running
			  FROM ops_pipeline_run r
			  LEFT JOIN ops_expected_task t ON t.pipeline_run_id = r.pipeline_run_id
			 WHERE r.created_at >= now() - (? * interval '1 day')
			 ORDER BY r.created_at ASC, r.pipeline_run_id ASC,
			          CASE t.stage WHEN 'raw' THEN 0 WHEN 'normalize' THEN 1 ELSE 2 END, t.task_key
			""";

	/**
	 * 레인(pipeline_type)별 최신 런 하나씩 — Run Overview(ALPHA-683). "최신"의 정의·동률 해소는
	 * {@code LATEST_RUN_SQL} 과 같다. LEFT JOIN 이유는 격자와 같다(작업 없는 런도 레인으로).
	 *
	 * <p>freshness 축(ADR-0043)은 원장 컬럼을 그대로 옮긴다 — writer(ALPHA-654) 배선 전엔
	 * 전부 NULL 이 정상이고, NULL(계약 미적용)과 UNKNOWN(증거 없음)을 뭉개지 않는다.
	 */
	private static final String OVERVIEW_SQL = """
			SELECT l.pipeline_type, l.run_key, l.launch_status, l.orchestration_status,
			       l.trading_date, l.created_at,
			       t.stage, t.task_key, t.plan_status, t.task_outcome, t.data_status,
			       t.required, t.deadline_at, t.failed_records,
			       t.freshness_status, t.expected_as_of_date, t.actual_as_of_date
			  FROM (SELECT DISTINCT ON (pipeline_type) pipeline_run_id, pipeline_type, run_key,
			               launch_status, orchestration_status, trading_date, created_at
			          FROM ops_pipeline_run
			         ORDER BY pipeline_type, created_at DESC, pipeline_run_id DESC) l
			  LEFT JOIN ops_expected_task t ON t.pipeline_run_id = l.pipeline_run_id
			 ORDER BY l.pipeline_type,
			          CASE t.stage WHEN 'raw' THEN 0 WHEN 'normalize' THEN 1 ELSE 2 END, t.task_key
			""";

	private final JdbcTemplate jdbc;

	public JdbcPipelineStatusRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	@Override
	@Transactional(readOnly = true)
	public List<OverviewLane> overview() {
		// 단일 SELECT — statement 스냅샷이 일관성을 보장한다(격자와 같은 이유).
		Map<String, OverviewHeader> headers = new LinkedHashMap<>();
		Map<String, List<OverviewTask>> tasks = new LinkedHashMap<>();
		jdbc.query(OVERVIEW_SQL, rs -> {
			String lane = rs.getString("pipeline_type");
			if (!headers.containsKey(lane)) {
				java.sql.Date tradingDate = rs.getDate("trading_date");
				headers.put(lane, new OverviewHeader(lane, rs.getString("run_key"),
						rs.getString("launch_status"), rs.getString("orchestration_status"),
						tradingDate == null ? null : tradingDate.toLocalDate(),
						rs.getObject("created_at", OffsetDateTime.class)));
				tasks.put(lane, new ArrayList<>());
			}
			if (rs.getString("task_key") != null) {
				java.sql.Date expectedAsOf = rs.getDate("expected_as_of_date");
				java.sql.Date actualAsOf = rs.getDate("actual_as_of_date");
				tasks.get(lane).add(new OverviewTask(
						rs.getString("stage"),
						rs.getString("task_key"),
						rs.getString("plan_status"),
						rs.getString("task_outcome"),
						rs.getString("data_status"),
						rs.getBoolean("required"),
						rs.getObject("deadline_at", OffsetDateTime.class),
						nullableLong(rs, "failed_records"),
						rs.getString("freshness_status"),
						expectedAsOf == null ? null : expectedAsOf.toLocalDate(),
						actualAsOf == null ? null : actualAsOf.toLocalDate()));
			}
		});
		return headers.entrySet().stream()
				.map(e -> new OverviewLane(e.getValue().pipelineType(), e.getValue().runKey(),
						e.getValue().launchStatus(), e.getValue().orchestrationStatus(),
						e.getValue().tradingDate(), e.getValue().plannedAt(),
						List.copyOf(tasks.get(e.getKey()))))
				.toList();
	}

	private record OverviewHeader(String pipelineType, String runKey, String launchStatus,
			String orchestrationStatus, LocalDate tradingDate, OffsetDateTime plannedAt) {
	}

	@Override
	@Transactional(readOnly = true)
	public List<GridSlot> grid(int days) {
		// 단일 SELECT 라 statement 스냅샷이 일관성을 보장한다 — 드릴다운(네 조회)과 달리
		// REPEATABLE READ 로 올릴 이유가 없다.
		Map<String, RunRow> headers = new LinkedHashMap<>();
		Map<String, List<GridCell>> cells = new LinkedHashMap<>();
		jdbc.query(GRID_SQL, rs -> {
			String runId = rs.getString("pipeline_run_id");
			if (!headers.containsKey(runId)) {
				headers.put(runId, mapRun(rs, 0));
				cells.put(runId, new ArrayList<>());
			}
			// LEFT JOIN 이라 작업 없는 런은 task 컬럼이 전부 NULL 인 한 행으로 온다.
			if (rs.getString("task_key") != null) {
				cells.get(runId).add(new GridCell(
						rs.getString("stage"),
						rs.getString("task_key"),
						rs.getString("plan_status"),
						rs.getString("task_outcome"),
						rs.getString("data_status"),
						nullableLong(rs, "records_out"),
						nullableLong(rs, "failed_records"),
						rs.getString("skip_reason"),
						rs.getString("outcome_reason"),
						rs.getBoolean("running")));
			}
		}, days);
		return headers.entrySet().stream()
				.map(e -> new GridSlot(e.getValue().runKey(), e.getValue().launchStatus(),
						e.getValue().orchestrationStatus(), e.getValue().tradingDate(),
						List.copyOf(cells.get(e.getKey()))))
				.toList();
	}

	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public Optional<PipelineRunStatus> latestRun() {
		return jdbc.query(LATEST_RUN_SQL, JdbcPipelineStatusRepository::mapRun).stream()
				.findFirst().map(this::assemble);
	}

	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public Optional<PipelineRunStatus> runByKey(String runKey) {
		return jdbc.query(RUN_BY_KEY_SQL, JdbcPipelineStatusRepository::mapRun, runKey).stream()
				.findFirst().map(this::assemble);
		// 없으면 empty — 서비스가 404 로 바꾼다. 빈 리포트로 대신하면 오타 친 런 키가 "원장이
		// 비어 있다"로 보여, 운영자가 없는 사실을 있는 것처럼 읽는다.
	}

	/**
	 * 헤더 한 행에 작업·시도·이슈를 붙인다. 세 조회 모두 헤더의 ID 를 필요로 해 순차 실행이다.
	 *
	 * <p>네 조회는 <b>한 스냅샷</b> 안에서 돈다(위 메서드의 REPEATABLE READ). 단일 SELECT 였을 땐
	 * statement 스냅샷이 공짜로 그걸 보장했는데, 쪼개면서 사라졌다 — 그대로 두면 시도를 읽은 뒤
	 * writer 가 커밋하고 작업을 읽어 "outcome 은 FULFILLED 인데 시도는 아직 RUNNING" 같은,
	 * <b>어느 시점에도 존재하지 않은 조합</b>이 한 화면에 조립된다.
	 */
	private PipelineRunStatus assemble(RunRow run) {
		Map<String, List<AttemptStatus>> attempts = attemptsByTask(run.pipelineRunId());
		List<TaskStatus> tasks = jdbc.query(TASKS_SQL,
				(rs, i) -> mapTask(rs, attempts), run.pipelineRunId());
		List<IssueStatus> issues = jdbc.query(ISSUES_SQL,
				JdbcPipelineStatusRepository::mapIssue,
				run.pipelineRunId(), run.runKey(), run.pipelineRunId());
		return new PipelineRunStatus(run.runKey(), run.launchStatus(), run.orchestrationStatus(),
				run.tradingDate(), tasks, issues);
	}

	private Map<String, List<AttemptStatus>> attemptsByTask(String pipelineRunId) {
		Map<String, List<AttemptStatus>> byTask = new HashMap<>();
		jdbc.query(ATTEMPTS_SQL, rs -> {
			// SQL 이 이미 시각 오름차순이라 도착 순서대로 append 하면 순서가 보존된다.
			byTask.computeIfAbsent(rs.getString("expected_task_id"), k -> new ArrayList<>())
					.add(new AttemptStatus(
							rs.getString("attempt_id"),
							nullableInt(rs, "attempt_number"),
							rs.getString("ecs_task_arn"),
							rs.getString("execution_status"),
							rs.getObject("started_at", OffsetDateTime.class),
							rs.getObject("finished_at", OffsetDateTime.class),
							nullableInt(rs, "exit_code"),
							rs.getString("failure_reason"),
							rs.getString("record_source")));
		}, pipelineRunId);
		return byTask;
	}

	private record RunRow(String pipelineRunId, String runKey, String launchStatus,
			String orchestrationStatus, LocalDate tradingDate) {
	}

	private static RunRow mapRun(ResultSet rs, int rowNum) throws SQLException {
		java.sql.Date tradingDate = rs.getDate("trading_date");
		return new RunRow(rs.getString("pipeline_run_id"), rs.getString("run_key"),
				rs.getString("launch_status"), rs.getString("orchestration_status"),
				tradingDate == null ? null : tradingDate.toLocalDate());
	}

	private static TaskStatus mapTask(ResultSet rs, Map<String, List<AttemptStatus>> attempts)
			throws SQLException {
		return new TaskStatus(
				rs.getString("stage"),
				rs.getString("task_key"),
				rs.getString("dataset"),
				rs.getString("plan_status"),
				rs.getString("task_outcome"),
				rs.getString("data_status"),
				// getLong 은 SQL NULL 을 0 으로 돌려준다 — wasNull 로 갈라야 "0건 처리"와
				// "신호 없음"이 화면에서 구분된다(ALPHA-182 의 NULL 계약).
				nullableLong(rs, "records_out"),
				nullableLong(rs, "failed_records"),
				mapCompleteness(rs),
				rs.getObject("expected_at", OffsetDateTime.class),
				rs.getObject("deadline_at", OffsetDateTime.class),
				rs.getObject("missed_at", OffsetDateTime.class),
				rs.getObject("fulfilled_at", OffsetDateTime.class),
				rs.getString("skip_reason"),
				rs.getString("outcome_reason"),
				attempts.getOrDefault(rs.getString("expected_task_id"), List.of()),
				rs.getString("current_attempt_id"));
	}

	private static CompletenessStatus mapCompleteness(ResultSet rs) throws SQLException {
		if (!rs.getBoolean("has_completeness")) {
			return null;
		}
		return new CompletenessStatus(
				nullableLong(rs, "completeness_expected"),
				nullableLong(rs, "completeness_received"),
				nullableLong(rs, "completeness_missing"));
	}

	private static IssueStatus mapIssue(ResultSet rs, int rowNum) throws SQLException {
		return new IssueStatus(
				rs.getString("issue_type"),
				rs.getString("scope"),
				rs.getString("task_key"),
				rs.getString("status"),
				rs.getInt("occurrence_count"),   // NOT NULL DEFAULT 1 — 박싱할 이유가 없다
				rs.getObject("first_seen_at", OffsetDateTime.class),
				rs.getObject("last_seen_at", OffsetDateTime.class),
				rs.getString("resolution_reason"));
	}

	private static Long nullableLong(ResultSet rs, String column) throws SQLException {
		long value = rs.getLong(column);
		return rs.wasNull() ? null : value;
	}

	/** {@code exit_code} 는 0 이 성공이라 NULL 을 0 으로 뭉개면 <b>모름이 성공으로 뒤집힌다</b>. */
	private static Integer nullableInt(ResultSet rs, String column) throws SQLException {
		int value = rs.getInt(column);
		return rs.wasNull() ? null : value;
	}
}
