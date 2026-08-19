package com.edge.superadmin;

import com.edge.superadmin.repository.ConsoleFactsRepository;
import com.edge.superadmin.repository.ConsoleFactsRepository.EntityResolutionPoint;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.tuple;

/** 엔티티 해소 추이의 날짜 grain·최신 성공 선택·10점 제한을 실 cloud 스키마로 검증한다. */
@Transactional
class JdbcEntityResolutionTrendRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	@Autowired
	private ConsoleFactsRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	private void insert(String id, String day, String slot, String outcome, Long total,
			Long resolved, String finishedAt, String updatedAt) {
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, orchestration_status, trading_date,
				       created_at, updated_at)
				VALUES (?, ?, 'news', ?, 'LAUNCHED', 'SUCCEEDED', ?::date,
				        ?::timestamptz, ?::timestamptz)
				""", "run-" + id, "news:" + day + "T" + slot, "exec-" + id, day,
				updatedAt, updatedAt);
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key,
				       stage, dataset, plan_status, task_outcome, data_status, required,
				       idempotency_key, entity_resolution_arguments_total,
				       entity_resolution_arguments_resolved, fulfilled_at, created_at, updated_at)
				VALUES (?, ?, 'LOAD_ASSERTIONS', 'feature', 'document_assertion', 'DUE', ?,
				        'VALID', true, ?, ?, ?, ?::timestamptz, ?::timestamptz, ?::timestamptz)
				""", "task-" + id, "run-" + id, outcome, "idem-" + id, total, resolved,
				"FULFILLED".equals(outcome) ? updatedAt : null, updatedAt, updatedAt);
		jdbc.update("""
				INSERT INTO ops_task_attempt (attempt_id, expected_task_id, ecs_task_arn,
				       execution_status, started_at, finished_at, exit_code,
				       entity_resolution_arguments_total, entity_resolution_arguments_resolved)
				VALUES (?, ?, ?, ?, ?::timestamptz, ?::timestamptz, ?, ?, ?)
				""", "attempt-" + id, "task-" + id, "arn:" + id,
				"FULFILLED".equals(outcome) ? "SUCCEEDED" : "FAILED", finishedAt, finishedAt,
				"FULFILLED".equals(outcome) ? 0 : 1, total, resolved);
		jdbc.update("UPDATE ops_expected_task SET current_attempt_id=? WHERE expected_task_id=?",
				"attempt-" + id, "task-" + id);
	}

	@Test
	void 같은_날은_최신_성공_pair만_남고_실패와_계측_없음은_점이_아니다() {
		insert("older", "2026-08-01", "09:00", "FULFILLED", 100L, 60L,
				"2026-08-01T06:40:00Z", "2026-08-01T08:00:00Z");
		/* 앞 런의 종료 기록은 Reconciler가 뒤늦게 복구할 수 있다. 그래도 mutable 종료 시각이 아니라
		 * 원장에 뒤에 생성된 런이 이겨야 한다. */
		insert("latest", "2026-08-01", "15:30", "FULFILLED", 100L, 70L,
				"2026-08-01T07:40:00Z", "2026-08-01T07:41:00Z");
		jdbc.update("UPDATE ops_pipeline_run SET created_at=?::timestamptz WHERE pipeline_run_id=?",
				"2026-08-01T06:30:00Z", "run-older");
		jdbc.update("UPDATE ops_task_attempt SET started_at=?::timestamptz, finished_at=?::timestamptz"
				+ " WHERE attempt_id=?", "2026-08-01T06:39:00Z", "2026-08-01T08:40:00Z",
				"attempt-older");
		jdbc.update("UPDATE ops_expected_task SET entity_resolution_arguments_total=1,"
				+ " entity_resolution_arguments_resolved=0 WHERE expected_task_id=?", "task-latest");
		/* 더 뒤에 생긴 실패 런을 먼저 고른 뒤 버리면 이 날의 마지막 성공 관측까지 사라진다. */
		insert("later-failed", "2026-08-01", "16:00", "FAILED", 100L, 1L,
				"2026-08-01T08:50:00Z", "2026-08-01T09:00:00Z");
		insert("failed", "2026-08-02", "15:30", "FAILED", 100L, 1L,
				"2026-08-02T06:40:00Z", "2026-08-02T06:41:00Z");
		insert("unmeasured", "2026-08-03", "15:30", "FULFILLED", null, null,
				"2026-08-03T06:40:00Z", "2026-08-03T06:41:00Z");
		insert("unfinished", "2026-08-03", "09:00", "FULFILLED", 100L, 99L,
				null, "2026-08-03T07:41:00Z");
		insert("zero", "2026-08-04", "15:30", "FULFILLED", 0L, 0L,
				"2026-08-04T06:40:00Z", "2026-08-04T06:41:00Z");

		assertThat(repository.entityResolutionTrend(LocalDate.parse("2026-08-04")))
				.extracting(EntityResolutionPoint::date, EntityResolutionPoint::totalArguments,
						EntityResolutionPoint::resolvedArguments)
				.containsExactly(
						tuple(LocalDate.parse("2026-08-01"), 100L, 70L),
						tuple(LocalDate.parse("2026-08-04"), 0L, 0L));
	}

	@Test
	void 시도_행이_없거나_reconciler가_백필한_성공은_같은_실행의_pair를_증명할_수_없어_제외한다() {
		insert("attempted", "2026-08-01", "09:00", "FULFILLED", 100L, 70L,
				"2026-08-01T07:40:00Z", "2026-08-01T07:41:00Z");
		jdbc.update("""
				INSERT INTO ops_task_attempt (attempt_id, expected_task_id, ecs_task_arn,
				       execution_status, started_at, finished_at, exit_code,
				       entity_resolution_arguments_total, entity_resolution_arguments_resolved)
				VALUES ('attempt-retry', 'task-attempted', 'arn:retry', 'SUCCEEDED',
				        '2026-08-01T07:50:00Z'::timestamptz, '2026-08-01T07:55:00Z'::timestamptz,
				        0, 100, 75)
				""");
		insert("no-attempt", "2026-08-01", "15:30", "FULFILLED", 100L, 80L,
				"2026-08-01T09:00:00Z", "2026-08-01T09:01:00Z");
		insert("backfill", "2026-08-01", "16:00", "FULFILLED", 100L, 90L,
				"2026-08-01T10:00:00Z", "2026-08-01T10:01:00Z");
		jdbc.update("UPDATE ops_task_attempt SET record_source='RECONCILER_BACKFILL'"
				+ " WHERE attempt_id=?", "attempt-backfill");
		jdbc.update("UPDATE ops_expected_task SET current_attempt_id=NULL WHERE expected_task_id=?",
				"task-no-attempt");
		jdbc.update("DELETE FROM ops_task_attempt WHERE attempt_id=?", "attempt-no-attempt");

		assertThat(repository.entityResolutionTrend(LocalDate.parse("2026-08-01")))
				.singleElement()
				.extracting(EntityResolutionPoint::resolvedArguments)
				.isEqualTo(75L);
	}

	@Test
	void 날짜_상한_이하의_최근_10개만_오래된_순으로_돌려준다() {
		for (int d = 1; d <= 12; d++) {
			String day = "2026-08-%02d".formatted(d);
			insert("d" + d, day, "15:30", "FULFILLED", 100L, (long) d,
					day + "T06:40:00Z", day + "T06:41:00Z");
		}

		assertThat(repository.entityResolutionTrend(LocalDate.parse("2026-08-11")))
				.extracting(EntityResolutionPoint::date)
				.containsExactly(
						LocalDate.parse("2026-08-02"), LocalDate.parse("2026-08-03"),
						LocalDate.parse("2026-08-04"), LocalDate.parse("2026-08-05"),
						LocalDate.parse("2026-08-06"), LocalDate.parse("2026-08-07"),
						LocalDate.parse("2026-08-08"), LocalDate.parse("2026-08-09"),
						LocalDate.parse("2026-08-10"), LocalDate.parse("2026-08-11"));
	}
}
