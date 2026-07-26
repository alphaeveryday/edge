package com.edge.superadmin.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

/**
 * {@link PipelineStatusRepository} 의 JdbcTemplate 구현 — 원장 조회 1회로 런 헤더와 작업 목록을
 * 함께 읽는다(ALPHA-514).
 */
@Repository
public class JdbcPipelineStatusRepository implements PipelineStatusRepository {

	/**
	 * 최신 런의 기대 작업 전부 + 각 작업의 <b>마지막 시도</b> 종료 시각.
	 *
	 * <p>시도를 LATERAL 로 붙이는 이유: 한 작업에 시도가 여러 개(재시도·Reconciler backfill)일 수
	 * 있는데 화면이 알아야 할 건 마지막 것 하나다. 단순 JOIN 이면 작업 행이 시도 수만큼 불어나
	 * 25행 화면이 조용히 중복된다.
	 *
	 * <p>stage 정렬을 CASE 로 고정하는 이유: 파이프라인 순서는 raw→normalize→feature 인데
	 * 문자열 정렬은 feature→normalize→raw 라 <b>역순</b>이 된다. 운영자는 앞 단계부터 읽는다.
	 *
	 * <p>"최신 런"의 정의는 {@code created_at} 내림차순이다 — 슬롯 키(ALPHA-564)가 시각이라
	 * 하루에 여러 런이 있을 수 있고, 그중 마지막으로 <b>계획된</b> 것이 현재 관심 대상이다.
	 *
	 * <p><b>동률 해소를 명시한다</b>(run 은 {@code pipeline_run_id}, 시도는 {@code attempt_id}) —
	 * {@code created_at}·{@code started_at} 에 유일성 제약이 없어서, 동률이면 LIMIT 1 이 매 조회마다
	 * 다른 행을 고를 수 있다. 새로고침할 때마다 화면이 흔들리면 운영자는 무엇이 사실인지 못 정한다.
	 */
	private static final String SQL = """
			SELECT r.run_key, r.launch_status, r.orchestration_status, r.trading_date,
			       t.stage, t.task_key, t.dataset, t.plan_status, t.task_outcome, t.data_status,
			       t.records_out, t.failed_records, a.execution_status, a.finished_at
			  FROM ops_expected_task t
			  JOIN ops_pipeline_run r ON r.pipeline_run_id = t.pipeline_run_id
			  LEFT JOIN LATERAL (
			       SELECT finished_at, execution_status FROM ops_task_attempt
			        WHERE expected_task_id = t.expected_task_id
			        ORDER BY started_at DESC NULLS LAST, attempt_id DESC
			        LIMIT 1) a ON TRUE
			 WHERE r.pipeline_run_id = (
			       SELECT pipeline_run_id FROM ops_pipeline_run
			        ORDER BY created_at DESC, pipeline_run_id DESC LIMIT 1)
			 ORDER BY CASE t.stage WHEN 'raw' THEN 0 WHEN 'normalize' THEN 1 ELSE 2 END, t.task_key
			""";

	private final JdbcTemplate jdbc;

	public JdbcPipelineStatusRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	@Override
	public Optional<PipelineRunStatus> latestRun() {
		List<Row> rows = jdbc.query(SQL, JdbcPipelineStatusRepository::mapRow);
		if (rows.isEmpty()) {
			return Optional.empty();   // 원장에 런이 없다(초기 환경) — 빈 화면이 정답이지 에러가 아니다
		}
		Row head = rows.getFirst();
		return Optional.of(new PipelineRunStatus(head.runKey(), head.launchStatus(),
				head.orchestrationStatus(), head.tradingDate(),
				rows.stream().map(Row::task).toList()));
	}

	/** 런 헤더는 모든 행에 같은 값으로 실려 온다(한 런만 조회하므로) — 첫 행에서만 읽는다. */
	private record Row(String runKey, String launchStatus, String orchestrationStatus,
			LocalDate tradingDate, TaskStatus task) {
	}

	private static Row mapRow(ResultSet rs, int rowNum) throws SQLException {
		java.sql.Date tradingDate = rs.getDate("trading_date");
		return new Row(
				rs.getString("run_key"),
				rs.getString("launch_status"),
				rs.getString("orchestration_status"),
				tradingDate == null ? null : tradingDate.toLocalDate(),
				new TaskStatus(
						rs.getString("stage"),
						rs.getString("task_key"),
						rs.getString("dataset"),
						rs.getString("plan_status"),
						rs.getString("task_outcome"),
						rs.getString("data_status"),
						rs.getString("execution_status"),
						// getLong 은 SQL NULL 을 0 으로 돌려준다 — wasNull 로 갈라야 "0건 처리"와
						// "신호 없음"이 화면에서 구분된다(ALPHA-182 의 NULL 계약).
						nullableLong(rs, "records_out"),
						nullableLong(rs, "failed_records"),
						rs.getObject("finished_at", OffsetDateTime.class)));
	}

	private static Long nullableLong(ResultSet rs, String column) throws SQLException {
		long value = rs.getLong(column);
		return rs.wasNull() ? null : value;
	}
}
