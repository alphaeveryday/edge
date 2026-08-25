package com.edge.superadmin;

import com.edge.superadmin.repository.PipelineStatusRepository;
import com.edge.superadmin.repository.PipelineStatusRepository.AttemptStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.CompletenessStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.IssueStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.PipelineRunStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.TaskStatus;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 원장 조회 SQL 통합 테스트 — 실 `ops_*` 스키마(Testcontainers + Flyway migrations-cloud)를
 * 대상으로 컬럼명·조인·최신행 선택·NULL 매핑이 실제로 맞는지 검증한다(ALPHA-514, 드릴다운 574).
 *
 * <p>손 페이크만으로는 이 조회를 <b>한 줄도 실행하지 않는다</b> — 컬럼명 오타나 조인 실수가
 * 전부 초록으로 통과하고 운영에서야 드러난다(Rule 9: 로직이 바뀌어도 못 깨지는 테스트는 잘못됐다).
 * 원장 테이블의 소유는 data-pipeline 이라 스키마가 남의 손에 바뀔 수 있다는 점이 이 테스트의
 * 값어치다.
 *
 * <p><b>여기서 검증되지 않는 것</b>(Rule 12 — 안 한 것을 한 것처럼 두지 않는다): 리포지토리의
 * {@code @Transactional(REPEATABLE_READ)} 스냅샷 보장. 이 클래스의 {@code @Transactional} 이 먼저
 * 트랜잭션을 열고 리포지토리가 거기 참여하므로, 안쪽의 격리수준·readOnly 속성은 <b>적용되지
 * 않는다</b>(Spring 기본값은 참여 시 정의 불일치를 검증하지 않는다). 즉 그 애너테이션이 지워져도
 * 이 테스트들은 전부 통과한다. 실제 보장을 확인하려면 조회 도중 커밋하는 별도 writer 가 필요한데,
 * 그 테스트는 타이밍 의존이라 여기 두지 않았다.
 */
@Transactional
class JdbcPipelineStatusRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	@Autowired
	private PipelineStatusRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	private void insertRun(String id, String runKey, String launchStatus, String orchestration,
			String tradingDate, String createdAt) {
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, orchestration_status, trading_date, created_at)
				VALUES (?,?,?,?,?,?,?::date,?::timestamptz)
				""", id, runKey, "etf-daily", "exec-" + id, launchStatus, orchestration,
				tradingDate, createdAt);
	}

	private void insertTask(String id, String runId, String stage, String taskKey, String dataset,
			String planStatus, String outcome, String dataStatus, Long recordsOut,
			Long failedRecords) {
		insertTask(id, runId, stage, taskKey, dataset, planStatus, outcome, dataStatus,
				recordsOut, null, failedRecords);
	}

	private void insertTask(String id, String runId, String stage, String taskKey, String dataset,
			String planStatus, String outcome, String dataStatus, Long recordsOut,
			Long unsupportedRecords, Long failedRecords) {
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, data_status, records_out,
				       unsupported_records, failed_records,
				       idempotency_key)
				VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
				""", id, runId, taskKey, stage, dataset, planStatus, outcome, dataStatus,
				recordsOut, unsupportedRecords, failedRecords, runId + taskKey);
	}

	private void insertAttempt(String id, String taskId, String arn, String finishedAt,
			String startedAt) {
		insertAttempt(id, taskId, arn, "SUCCEEDED", finishedAt, startedAt);
	}

	private void insertAttempt(String id, String taskId, String arn, String execStatus,
			String finishedAt, String startedAt) {
		jdbc.update("""
				INSERT INTO ops_task_attempt (attempt_id, expected_task_id, ecs_task_arn,
				       execution_status, started_at, finished_at)
				VALUES (?,?,?,?,?::timestamptz,?::timestamptz)
				""", id, taskId, arn, execStatus, startedAt, finishedAt);
	}

	private void insertIssue(String id, String type, String scope, String scopeKey,
			String status) {
		jdbc.update("""
				INSERT INTO ops_reconciliation_issue (issue_id, issue_type, scope, scope_key,
				       dedupe_key, status, occurrence_count)
				VALUES (?,?,?,?,?,?,?)
				""", id, type, scope, scopeKey, id, status, 2);
	}

	private void setCompleteness(String taskId, String completeness) {
		jdbc.update("UPDATE ops_expected_task SET completeness=?::jsonb WHERE expected_task_id=?",
				completeness, taskId);
	}

	@Test
	void 최신_런의_모든_축을_컬럼명_그대로_읽는다() {
		insertRun("r1", "etf-daily:2026-07-27T15:40", "LAUNCHED", "FAILED", "2026-07-27",
				"2026-07-27T06:40:00Z");
		insertTask("t1", "r1", "feature", "LOAD_ETF_HOLDINGS", "etf_holding_snapshot", "DUE", "FULFILLED",
				"INCOMPLETE", 2736L, 42L, 4L);
		insertAttempt("a1", "t1", "arn:aws:ecs:task/1", "2026-07-27T06:45:00Z",
				"2026-07-27T06:41:00Z");

		PipelineRunStatus run = repository.latestRun().orElseThrow();

		assertThat(run.runKey()).isEqualTo("etf-daily:2026-07-27T15:40");
		assertThat(run.launchStatus()).isEqualTo("LAUNCHED");
		assertThat(run.orchestrationStatus()).isEqualTo("FAILED");
		assertThat(run.tradingDate()).isEqualTo("2026-07-27");
		assertThat(run.tasks()).singleElement().satisfies(t -> {
			assertThat(t.taskKey()).isEqualTo("LOAD_ETF_HOLDINGS");
			assertThat(t.stage()).isEqualTo("feature");
			assertThat(t.dataset()).isEqualTo("etf_holding_snapshot");
			assertThat(t.planStatus()).isEqualTo("DUE");
			assertThat(t.outcome()).isEqualTo("FULFILLED");
			// 실행은 성공(FULFILLED)인데 데이터는 불완전하다 — 이 두 축이 함께 와야
			// 화면이 "완료"를 온전한 초록으로 그리지 않는다.
			assertThat(t.dataStatus()).isEqualTo("INCOMPLETE");
			assertThat(t.currentAttempt().executionStatus()).isEqualTo("SUCCEEDED");
			assertThat(t.recordsOut()).isEqualTo(2736L);
			assertThat(t.unsupportedRecords()).isEqualTo(42L);
			assertThat(t.failedRecords()).isEqualTo(4L);
			assertThat(t.currentAttempt().finishedAt()).isNotNull();
		});
	}

	@Test
	void 건수가_NULL_인_행은_0_이_아니라_null_로_온다() {
		// WHY: getLong 은 SQL NULL 을 0 으로 돌려준다. wasNull 처리를 빼면 "신호 없음"이
		//      "0건 처리"로 바뀌어 화면에서 구분이 사라진다(ALPHA-182 계약의 마지막 관문).
		insertRun("r2", "etf-daily:2026-07-27T15:41", "LAUNCHED", null, null,
				"2026-07-27T07:00:00Z");
		insertTask("t2", "r2", "feature", "TAG_NEWS", "news_assertions", "DUE", "FULFILLED",
				"UNKNOWN", null, null);

		TaskStatus task = repository.latestRun().orElseThrow().tasks().getFirst();

		assertThat(task.recordsOut()).isNull();
		assertThat(task.unsupportedRecords()).isNull();
		assertThat(task.failedRecords()).isNull();
		assertThat(task.completeness()).isNull();
		assertThat(task.attempts()).isEmpty();       // 시도가 없으면 빈 목록이다(null 아님)
		assertThat(task.currentAttempt()).isNull();
	}

	@Test
	void ETF_완전성_JSONB의_저장값과_null_경계를_그대로_읽는다() {
		// WHY: 객체 없음(미배선)과 객체 안의 수신값 없음(기대 스냅샷은 있음)은 다른 사실이다.
		//      SQL에서 COALESCE하거나 차이를 재계산하면 둘 다 "0개 수신"으로 왜곡된다.
		insertRun("r-completeness", "etf-daily:2026-07-27T15:42", "LAUNCHED", "SUCCEEDED",
				"2026-07-27", "2026-07-27T08:00:00Z");
		insertTask("t-complete", "r-completeness", "raw", "ETF_HOLDINGS_COLLECTION_KRX",
				"etf_holdings", "DUE", "FULFILLED", "INCOMPLETE", 4120L, 0L);
		// missing 을 일부러 33-32 와 다르게 둔다 — 이 값이 1 이면 재계산하는 리더도 통과해
		// "원장 값 그대로"를 못 잠근다. 원장이 낸 판정이 정본이지 뺄셈 결과가 아니다.
		setCompleteness("t-complete", "{\"expected\":33,\"received\":32,\"missing\":3}");
		insertTask("t-unknown", "r-completeness", "raw", "NAV_COLLECTION_KIS",
				"etf_nav", "DUE", "FULFILLED", "UNKNOWN", null, null);
		setCompleteness("t-unknown", "{\"expected\":33,\"received\":null,\"missing\":null}");
		insertTask("t-unwired", "r-completeness", "feature", "TAG_NEWS",
				"news_assertions", "DUE", "FULFILLED", "UNKNOWN", null, null);

		List<TaskStatus> tasks = repository.latestRun().orElseThrow().tasks();
		TaskStatus complete = tasks.stream()
				.filter(t -> t.taskKey().equals("ETF_HOLDINGS_COLLECTION_KRX"))
				.findFirst().orElseThrow();
		TaskStatus unknown = tasks.stream()
				.filter(t -> t.taskKey().equals("NAV_COLLECTION_KIS"))
				.findFirst().orElseThrow();
		TaskStatus unwired = tasks.stream()
				.filter(t -> t.taskKey().equals("TAG_NEWS"))
				.findFirst().orElseThrow();

		assertThat(complete.completeness()).isEqualTo(new CompletenessStatus(33L, 32L, 3L));
		assertThat(unknown.completeness()).isEqualTo(new CompletenessStatus(33L, null, null));
		assertThat(unwired.completeness()).isNull();
	}

	private void setCurrentAttempt(String taskId, String attemptId) {
		jdbc.update("UPDATE ops_expected_task SET current_attempt_id=? WHERE expected_task_id=?",
				attemptId, taskId);
	}

	@Test
	void 현재_시도는_시각_순서가_아니라_원장이_지목한_것이다() {
		// WHY: Reconciler 의 사후 복구는 실제 실행 시각을 모른 채 started_at 에 **복구 시각**을
		//      넣는다(ledger.py backfill_attempt 의 `now()`). 그래서 뒤늦게 복구된 **옛 실패 시도**가
		//      시각순으로는 맨 뒤에 온다 — 순서로 고르면 이미 재시도로 성공한 작업이 화면에서
		//      실패로 보인다. 원장의 current_attempt_id 가 이 질문에 이미 답을 갖고 있다.
		insertRun("r12", "etf-daily:2026-07-27T15:51", "LAUNCHED", "SUCCEEDED", null,
				"2026-07-27T16:00:00Z");
		insertTask("t12", "r12", "raw", "NAV_COLLECTION_KIS", "etf_nav", "DUE", "FULFILLED",
				"VALID", 30L, 0L);
		// 실제로 먼저 돌아 성공한 시도(wrapper 기록).
		insertAttempt("a12ok", "t12", "arn:aws:ecs:task/12ok", "2026-07-27T16:10:00Z",
				"2026-07-27T16:05:00Z");
		// 그보다 **앞서 있었던** 실패를 Reconciler 가 뒤늦게 복구 — started_at 이 복구 시각이라
		// 시각순으로는 마지막이다.
		insertAttempt("a12old", "t12", "arn:aws:ecs:task/12old", "FAILED",
				"2026-07-27T17:00:00Z", "2026-07-27T17:00:00Z");
		jdbc.update("UPDATE ops_task_attempt SET record_source='RECONCILER_BACKFILL' "
				+ "WHERE attempt_id='a12old'");
		setCurrentAttempt("t12", "a12ok");

		TaskStatus task = repository.latestRun().orElseThrow().tasks().getFirst();

		// 순서상 마지막은 복구된 실패지만, 현재 상태는 원장이 지목한 성공이다.
		assertThat(task.attempts().getLast().executionStatus()).isEqualTo("FAILED");
		assertThat(task.currentAttempt().executionStatus()).isEqualTo("SUCCEEDED");
		assertThat(task.currentAttempt().finishedAt().toInstant())
				.isEqualTo(java.time.Instant.parse("2026-07-27T16:10:00Z"));
	}

	@Test
	void 지목이_낡아도_실행_중인_시도가_있으면_그것이_현재다() {
		// WHY: Reconciler 는 사후 복구 후 outcome 만 갱신하고 current_attempt_id 는 안 건드린다
		//      (reconciler.py _judge_outcome). 그래서 지목은 낡을 수 있다 — 지목을 무조건 믿으면
		//      **지금 돌고 있는데도** 화면이 옛 실패를 현재 상태로 말한다. "RUNNING 인 시도가 있다"는
		//      순서·지목과 무관하게 참이므로 그것부터 본다.
		insertRun("r13", "etf-daily:2026-07-27T15:52", "LAUNCHED", "RUNNING", null,
				"2026-07-27T18:00:00Z");
		insertTask("t13", "r13", "raw", "PRICE_COLLECTION_KIS", "price_daily", "DUE", "FAILED",
				"UNKNOWN", null, null);
		insertAttempt("a13fail", "t13", "arn:aws:ecs:task/13f", "FAILED",
				"2026-07-27T18:10:00Z", "2026-07-27T18:05:00Z");
		insertAttempt("a13run", "t13", "arn:aws:ecs:task/13r", "RUNNING", null,
				"2026-07-27T18:20:00Z");
		// 강제 종료로 남은 옛 RUNNING 이 있어도(아래가 더 나중) 지금 도는 것을 가리면 안 된다.
		insertAttempt("a13run2", "t13", "arn:aws:ecs:task/13r2", "RUNNING", null,
				"2026-07-27T18:40:00Z");
		setCurrentAttempt("t13", "a13fail");   // 낡은 지목

		TaskStatus task = repository.latestRun().orElseThrow().tasks().getFirst();

		assertThat(task.currentAttempt().executionStatus()).isEqualTo("RUNNING");
		assertThat(task.currentAttempt().ecsTaskArn()).isEqualTo("arn:aws:ecs:task/13r2");
		// 이전 실패는 지워지지 않는다 — 두 사실이 동시에 참이다(화면이 "재시도 중"을 함께 낸다).
		assertThat(task.outcome()).isEqualTo("FAILED");
	}

	@Test
	void 시도가_여러_개면_전량을_시간순으로_싣고_지목이_없으면_마지막이_현재다() {
		// WHY: 예전 구현은 LATERAL 로 마지막 한 건만 남겨, **실패 후 재시도로 성공한 작업**이
		//      처음부터 성공한 것과 구분되지 않았다. 사후 복구(RECONCILER_BACKFILL)도 같이 묻혔다.
		//      current_attempt_id 가 비어 있을 때(사후 복구만 있는 작업 등)의 대체 경로도 함께 잠근다.
		insertRun("r3", "etf-daily:2026-07-27T15:42", "LAUNCHED", "SUCCEEDED", null,
				"2026-07-27T08:00:00Z");
		insertTask("t3", "r3", "normalize", "NORMALIZE_NEWS", "news_articles", "DUE", "FULFILLED",
				"VALID", 10L, 0L);
		insertAttempt("a3a", "t3", "arn:aws:ecs:task/3a", "FAILED", "2026-07-27T08:10:00Z",
				"2026-07-27T08:05:00Z");
		insertAttempt("a3b", "t3", "arn:aws:ecs:task/3b", "2026-07-27T08:30:00Z",
				"2026-07-27T08:25:00Z");
		jdbc.update("UPDATE ops_task_attempt SET exit_code=1, failure_reason=?, "
				+ "record_source='RECONCILER_BACKFILL' WHERE attempt_id='a3a'", "ecs exit 1");

		PipelineRunStatus run = repository.latestRun().orElseThrow();

		assertThat(run.tasks()).hasSize(1);          // 행이 시도 수만큼 불어나지 않는다
		List<AttemptStatus> attempts = run.tasks().getFirst().attempts();
		assertThat(attempts).hasSize(2);
		assertThat(attempts.getFirst().executionStatus()).isEqualTo("FAILED");
		assertThat(attempts.getFirst().exitCode()).isEqualTo(1);
		assertThat(attempts.getFirst().failureReason()).isEqualTo("ecs exit 1");
		// 원장이 스스로 메운 행과 실제 관측된 실행을 가르는 유일한 신호다.
		assertThat(attempts.getFirst().recordSource()).isEqualTo("RECONCILER_BACKFILL");
		assertThat(attempts.getLast().recordSource()).isEqualTo("WRAPPER");
		assertThat(run.tasks().getFirst().currentAttempt().finishedAt().toInstant())
				.isEqualTo(java.time.Instant.parse("2026-07-27T08:30:00Z"));
	}

	@Test
	void exit_code_가_NULL_이면_0_이_아니라_null_이다() {
		// WHY: exit_code 는 0 이 성공이다. getInt 가 NULL 을 0 으로 돌려주므로 wasNull 을 빼면
		//      **모름이 성공으로 뒤집힌다** — 원장이 관대해지는 쪽이라 조용히 통과한다.
		insertRun("r8", "etf-daily:2026-07-27T15:47", "LAUNCHED", "RUNNING", null,
				"2026-07-27T13:00:00Z");
		insertTask("t8", "r8", "raw", "NAV_COLLECTION_KIS", "etf_nav", "DUE", "PENDING",
				"UNKNOWN", null, null);
		insertAttempt("a8", "t8", "arn:aws:ecs:task/8", "RUNNING", null, "2026-07-27T13:05:00Z");

		AttemptStatus attempt = repository.latestRun().orElseThrow().tasks().getFirst()
				.currentAttempt();

		assertThat(attempt.exitCode()).isNull();
		assertThat(attempt.attemptNumber()).isNull();   // 표시용이라 writer 가 안 채울 수 있다
		assertThat(attempt.finishedAt()).isNull();
	}

	@Test
	void 이슈를_세_스코프_모두에서_모으고_task_는_작업_이름으로_바꾼다() {
		// WHY: Reconciler 는 scope 별로 다른 키를 쓴다(run→pipeline_run_id, task→expected_task_id,
		//      slot→run_key). 한 스코프라도 빠지면 그 종류의 이슈는 화면에서 영영 안 보인다 —
		//      dev 의 거짓 LEDGER_GAP 17건이 콘솔 어디에도 안 뜨던 상태가 정확히 이것이다.
		insertRun("r9", "etf-daily:2026-07-27T15:48", "LAUNCHED", "FAILED", null,
				"2026-07-27T14:00:00Z");
		insertTask("t9", "r9", "raw", "PRICE_COLLECTION_KIS", "price_daily", "DUE", "MISSED",
				null, null, null);
		insertIssue("i-run", "LAUNCH_CONFLICT", "run", "r9", "OPEN");
		insertIssue("i-task", "LEDGER_GAP", "task", "t9", "OPEN");
		insertIssue("i-slot", "PLANNER_MISSING", "slot", "etf-daily:2026-07-27T15:48", "RESOLVED");
		// 다른 런의 이슈 — 스코프 키가 겹치지 않으므로 새어 들어오면 안 된다.
		insertIssue("i-other", "MISSED", "task", "t-other", "OPEN");

		List<IssueStatus> issues = repository.latestRun().orElseThrow().issues();

		assertThat(issues).extracting(IssueStatus::issueType)
				.containsExactlyInAnyOrder("LAUNCH_CONFLICT", "LEDGER_GAP", "PLANNER_MISSING");
		assertThat(issues).filteredOn(i -> "task".equals(i.scope())).singleElement()
				// 내부 ID(t9)가 아니라 운영자가 아는 이름으로 나온다.
				.satisfies(i -> assertThat(i.taskKey()).isEqualTo("PRICE_COLLECTION_KIS"));
		assertThat(issues).filteredOn(i -> "run".equals(i.scope())).singleElement()
				.satisfies(i -> {
					assertThat(i.taskKey()).isNull();
					assertThat(i.occurrenceCount()).isEqualTo(2);
					assertThat(i.firstSeenAt()).isNotNull();
				});
		// OPEN 이 먼저 온다 — 지금 문제인 것과 지나간 이력이 같은 자리에 섞이지 않는다.
		assertThat(issues.getLast().status()).isEqualTo("RESOLVED");
	}

	@Test
	void 런_키로_지목하면_최신이_아닌_그_런을_읽는다() {
		insertRun("r10old", "etf-daily:2026-07-27T15:50", "LAUNCHED", "SUCCEEDED", null,
				"2026-07-27T06:00:00Z");
		insertTask("t10old", "r10old", "raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "DUE",
				"FULFILLED", "VALID", 1L, 0L);
		insertRun("r10new", "etf-daily:2026-07-27T16:40", "LAUNCHED", "RUNNING", null,
				"2026-07-27T09:00:00Z");
		insertTask("t10new", "r10new", "raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "DUE",
				"PENDING", "UNKNOWN", null, null);

		PipelineRunStatus run =
				repository.runByKey("etf-daily:2026-07-27T15:50").orElseThrow();

		assertThat(run.orchestrationStatus()).isEqualTo("SUCCEEDED");
		assertThat(run.tasks()).singleElement()
				.satisfies(t -> assertThat(t.outcome()).isEqualTo("FULFILLED"));
	}

	@Test
	void 없는_런_키는_empty_다() {
		// WHY: 서비스가 이걸 404 로 바꾼다. 여기서 아무 런이나 돌려주면 오타 친 키가 남의 런을
		//      보여주고, 빈 리포트로 뭉개면 "원장이 비어 있다"로 읽힌다 — 셋 다 다른 사실이다.
		insertRun("r11", "etf-daily:2026-07-27T15:49", "LAUNCHED", "SUCCEEDED", null,
				"2026-07-27T15:00:00Z");

		assertThat(repository.runByKey("etf-daily:없는런")).isEmpty();
	}

	@Test
	void 여러_런_중_가장_최근에_계획된_런만_본다() {
		// WHY: 슬롯 키가 분 단위(ALPHA-564)라 하루에 여러 런이 공존한다. 옛 런을 섞어 보여주면
		//      운영자가 "지금 상태"를 못 읽는다.
		insertRun("r4old", "etf-daily:2026-07-27T15:40", "LAUNCHED", "SUCCEEDED", null,
				"2026-07-27T06:00:00Z");
		insertTask("t4old", "r4old", "raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "DUE",
				"FULFILLED", "VALID", 1L, 0L);
		insertRun("r4new", "etf-daily:2026-07-27T16:40", "LAUNCHED", "RUNNING", null,
				"2026-07-27T09:00:00Z");
		insertTask("t4new", "r4new", "raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "DUE",
				"PENDING", "UNKNOWN", null, null);

		PipelineRunStatus run = repository.latestRun().orElseThrow();

		assertThat(run.runKey()).isEqualTo("etf-daily:2026-07-27T16:40");
		assertThat(run.tasks()).singleElement()
				.satisfies(t -> assertThat(t.outcome()).isEqualTo("PENDING"));
	}

	@Test
	void 시도_시각이_동률이거나_NULL_이어도_결과가_흔들리지_않는다() {
		// WHY: started_at·created_at 에 유일성 제약이 없다. 동률 해소(attempt_id·pipeline_run_id)를
		//      빼면 "마지막 시도"가 매 조회마다 달라져 **새로고침할 때마다 화면이 바뀐다**.
		//      시각이 서로 다른 픽스처만 쓰면 그 보장을 빼도 테스트가 통과한다(Rule 9).
		insertRun("r5", "etf-daily:2026-07-27T15:43", "LAUNCHED", "SUCCEEDED", null,
				"2026-07-27T10:00:00Z");
		insertTask("t5", "r5", "raw", "NAV_COLLECTION_KIS", "etf_nav", "DUE", "FULFILLED",
				"VALID", 30L, 0L);
		// 같은 started_at 두 건 + started_at 이 NULL 인 한 건. NULL 은 스키마가 허용하는 값이라
		// 정렬이 방어해야 한다 — 현행 writer 는 wrapper·backfill 모두 채우지만(backfill 은
		// now() 고정), 그 사실에 정렬 정확성을 의존하면 writer 가 바뀌는 날 조용히 깨진다.
		insertAttempt("a5a", "t5", "arn:aws:ecs:task/5a", "2026-07-27T10:10:00Z",
				"2026-07-27T10:05:00Z");
		insertAttempt("a5b", "t5", "arn:aws:ecs:task/5b", "2026-07-27T10:20:00Z",
				"2026-07-27T10:05:00Z");
		insertAttempt("a5c", "t5", "arn:aws:ecs:task/5c", "2026-07-27T10:30:00Z", null);

		PipelineRunStatus run = repository.latestRun().orElseThrow();

		assertThat(run.tasks()).hasSize(1);
		// `NULLS FIRST` 제거는 **결정적으로** 여기서 깨진다 — NULL 이 ASC 기본값에서 맨 뒤로 가
		// a5c 의 10:30 이 최신으로 뽑힌다. `attempt_id ASC` 제거는 동률(a5a·a5b) 중 선택이 임의가
		// 되므로 이 단언이 "언젠가" 깨진다 — 같은 트랜잭션에서 조회를 반복해도 실행계획이 그대로라
		// 비결정성이 재현되지 않으므로, 반복 루프로 확신을 꾸미지 않는다.
		assertThat(run.tasks().getFirst().currentAttempt().finishedAt().toInstant())
				.isEqualTo(java.time.Instant.parse("2026-07-27T10:20:00Z"));
	}

	@Test
	void 계획_시각이_동률인_두_런에서도_같은_런을_고른다() {
		// WHY: created_at 동률이면 pipeline_run_id 타이브레이크가 없을 때 조회마다 다른 런이 온다.
		insertRun("r6a", "etf-daily:2026-07-27T15:44", "LAUNCHED", "SUCCEEDED", null,
				"2026-07-27T11:00:00Z");
		insertTask("t6a", "r6a", "raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "DUE",
				"FULFILLED", "VALID", 1L, 0L);
		insertRun("r6b", "etf-daily:2026-07-27T15:45", "LAUNCHED", "RUNNING", null,
				"2026-07-27T11:00:00Z");
		insertTask("t6b", "r6b", "raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "DUE",
				"PENDING", "UNKNOWN", null, null);

		// pipeline_run_id DESC 라 'r6b' 가 이긴다. 타이브레이커를 빼면 이 단언이 임의로 깨진다.
		assertThat(repository.latestRun().orElseThrow().runKey())
				.isEqualTo("etf-daily:2026-07-27T15:45");
	}

	@Test
	void 실행_중인_작업은_outcome_PENDING_옆에_RUNNING_시도를_함께_낸다() {
		// WHY: outcome 은 wrapper 가 **끝날 때** 쓴다. 실행 중엔 PENDING 인 채로 시도만 RUNNING 이라,
		//      execution_status 를 안 실으면 런이 도는 내내 진행 중 작업이 "아직 시작도 안 함"과
		//      같은 값으로 보인다 — 운영자가 화면을 보는 바로 그 시점이다(Codex #297 P2).
		insertRun("r7", "etf-daily:2026-07-27T15:46", "LAUNCHED", "RUNNING", null,
				"2026-07-27T12:00:00Z");
		insertTask("t7", "r7", "raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "DUE",
				"PENDING", "UNKNOWN", null, null);
		insertAttempt("a7", "t7", "arn:aws:ecs:task/7", "RUNNING", null, "2026-07-27T12:05:00Z");

		TaskStatus task = repository.latestRun().orElseThrow().tasks().getFirst();

		assertThat(task.outcome()).isEqualTo("PENDING");
		assertThat(task.currentAttempt().executionStatus()).isEqualTo("RUNNING");
		assertThat(task.currentAttempt().finishedAt()).isNull();   // 아직 안 끝났다
	}

	/** 격자의 창은 now() 기준이라, 고정 날짜 픽스처는 시간이 지나면 창 밖으로 새어 테스트가 썩는다. */
	private String daysAgo(int days) {
		return java.time.OffsetDateTime.now().minusDays(days).toString();
	}

	@Test
	void 격자는_창_안의_런만_계획순으로_작업까지_묶어_읽는다() {
		// 창 밖(40일 전) — 결과에 나오면 안 된다.
		insertRun("g-out", "etf-daily:2026-06-17T15:40", "LAUNCHED", "SUCCEEDED", null,
				daysAgo(40));
		insertTask("gt-out", "g-out", "raw", "PRICE_COLLECTION_KIS", "price_daily", "DUE",
				"FULFILLED", "VALID", 1L, 0L);
		// 창 안 두 런 — 삽입은 최신부터 하지만 결과는 계획 시각 오름차순이어야 한다.
		insertRun("g-new", "etf-daily:2026-07-27T15:40", "LAUNCHED", "FAILED", "2026-07-27",
				daysAgo(1));
		// stage 를 feature→raw 순으로 삽입 — 문자열 정렬이면 feature 가 앞이라 CASE 정렬이 검증된다.
		insertTask("gt-new-f", "g-new", "feature", "LOAD_ETF_HOLDINGS", "etf_holding_snapshot", "DUE",
				"FULFILLED", "UNKNOWN", null, 42L, null);
		insertTask("gt-new-r", "g-new", "raw", "PRICE_COLLECTION_KIS", "price_daily", "DUE",
				"FAILED", "UNKNOWN", null, 42L, 4L);
		insertRun("g-old", "etf-daily:2026-07-26T15:40", "LAUNCHED", "SUCCEEDED", "2026-07-26",
				daysAgo(2));
		insertTask("gt-old", "g-old", "raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "SKIPPED",
				null, null, null, null);
		jdbc.update("UPDATE ops_expected_task SET skip_reason='NON_TRADING_DAY' "
				+ "WHERE expected_task_id='gt-old'");
		// WHY: outcome 은 wrapper 가 끝날 때 쓴다 — 실행 중엔 시도만 RUNNING 이라, 이 신호를 안
		//      실으면 런이 도는 내내 진행 중 작업이 "시작 전"과 같은 셀이 된다. 끝난 시도가
		//      섞여 있어도(첫 시도 실패 후 재시도) 귀결 전이면 참이어야 한다.
		insertTask("gt-new-p", "g-new", "feature", "ASSEMBLE_EVENTS", "events", "DUE", "PENDING",
				"UNKNOWN", null, null);
		insertAttempt("ga-done", "gt-new-p", "arn:aws:ecs:task/g1", "FAILED",
				daysAgo(1), daysAgo(1));
		insertAttempt("ga-run", "gt-new-p", "arn:aws:ecs:task/g2", "RUNNING", null, daysAgo(1));
		// WHY: 반대로 귀결이 이미 적힌 작업은 RUNNING **잔재**(강제 종료로 안 닫힌 시도)가 있어도
		//      false 여야 한다 — 존재만 보면 죽은 시도가 판정 끝난 셀을 **영구히** "실행 중"으로
		//      만든다(격자엔 드릴다운의 STALLED 이슈 표 같은 완화 장치가 없다).
		insertAttempt("ga-dead", "gt-new-r", "arn:aws:ecs:task/g3", "RUNNING", null, daysAgo(1));

		List<PipelineStatusRepository.GridSlot> slots = repository.grid(30);

		assertThat(slots).extracting(PipelineStatusRepository.GridSlot::runKey)
				.containsExactly("etf-daily:2026-07-26T15:40", "etf-daily:2026-07-27T15:40");
		// 셀은 런별로 묶이고, 한 런 안에서는 파이프라인 순서(raw→feature)다.
		assertThat(slots.getLast().tasks())
				.extracting(PipelineStatusRepository.GridCell::taskKey)
				.containsExactly("PRICE_COLLECTION_KIS", "ASSEMBLE_EVENTS", "LOAD_ETF_HOLDINGS");
		assertThat(slots.getFirst().tasks()).singleElement().satisfies(c -> {
			assertThat(c.planStatus()).isEqualTo("SKIPPED");
			assertThat(c.outcome()).isNull();
			assertThat(c.skipReason()).isEqualTo("NON_TRADING_DAY");
		});
		// 건수 NULL 은 격자 경로에서도 0 으로 뭉개지지 않는다(ALPHA-182).
		assertThat(slots.getLast().tasks().getFirst().recordsOut()).isNull();
		assertThat(slots.getLast().tasks().getFirst().unsupportedRecords()).isNull();
		assertThat(slots.getLast().tasks().getFirst().failedRecords()).isEqualTo(4L);
		assertThat(slots.getLast().tasks().getLast().unsupportedRecords()).isEqualTo(42L);
		// 실행 중 신호 — 귀결 전(PENDING)이면서 RUNNING 시도가 있는 작업만 참이다.
		assertThat(slots.getLast().tasks().get(1).running()).isTrue();     // PENDING + RUNNING
		assertThat(slots.getLast().tasks().getFirst().running()).isFalse(); // FAILED + 죽은 RUNNING 잔재
		assertThat(slots.getLast().tasks().getLast().running()).isFalse();  // 시도 없음
		assertThat(slots.getFirst().tasks().getFirst().running()).isFalse();
		assertThat(slots.getLast().launchStatus()).isEqualTo("LAUNCHED");
		assertThat(slots.getLast().orchestrationStatus()).isEqualTo("FAILED");
		assertThat(slots.getLast().tradingDate()).isEqualTo("2026-07-27");
	}

	@Test
	void 기대작업이_없는_런도_격자_슬롯으로_온다() {
		// WHY: 기동 실패 런은 기대 작업이 안 적힐 수 있다. INNER JOIN 이면 이 슬롯이 격자에서
		//      통째로 사라진다 — "아예 못 뜬 런"이야말로 이 화면이 놓치면 안 되는 열이다.
		insertRun("g-empty", "etf-daily:2026-07-27T16:00", "LAUNCH_FAILED", null, null,
				daysAgo(1));

		List<PipelineStatusRepository.GridSlot> slots = repository.grid(30);

		assertThat(slots).singleElement().satisfies(s -> {
			assertThat(s.runKey()).isEqualTo("etf-daily:2026-07-27T16:00");
			assertThat(s.launchStatus()).isEqualTo("LAUNCH_FAILED");
			assertThat(s.orchestrationStatus()).isNull();
			assertThat(s.tasks()).isEmpty();
		});
	}

	@Test
	void 원장이_비어있으면_empty_다() {
		// WHY: 초기 환경은 장애가 아니다. 예외를 던지면 콘솔 페이지가 통째로 안 뜬다.
		jdbc.update("DELETE FROM ops_pipeline_run");

		Optional<PipelineRunStatus> run = repository.latestRun();

		assertThat(run).isEmpty();
	}

	/* ---------- Run Overview (ALPHA-683) ---------- */

	private void insertRunOfType(String id, String pipelineType, String runKey,
			String createdAt) {
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, orchestration_status, trading_date, created_at)
				VALUES (?,?,?,?,?,?,?::date,?::timestamptz)
				""", id, runKey, pipelineType, "exec-" + id, "LAUNCHED", "SUCCEEDED",
				"2026-07-27", createdAt);
	}

	@Test
	void overview_는_레인별_최신_슬롯만_고르고_작업을_단계_순으로_낸다() {
		// WHY: 손 페이크 테스트는 이미 정렬된 레인을 주입해 OVERVIEW_SQL 의 DISTINCT ON(최신 런
		//      선택)·stage 정렬을 한 줄도 실행하지 않는다 — 여기서 실 SQL 을 잠근다.
		//      "최신"의 축은 **슬롯 시각(run_key)** 이다: m1 은 과거 슬롯(07-26)을 **나중에**
		//      백필한 런(created_at 이 가장 최신)이다 — created_at 으로 고르면 백필이 오늘 정규
		//      런을 밀어내고 레인의 "최신"이 된다(봇 P2).
		insertRunOfType("m1", "etf-daily", "etf-daily:2026-07-26T15:40", "2026-07-28T06:40:00Z");
		insertRunOfType("m2", "etf-daily", "etf-daily:2026-07-27T15:40", "2026-07-27T06:40:00Z");
		insertRunOfType("n1", "news", "news:2026-07-27T15:30", "2026-07-27T06:30:00Z");
		// 파이프라인 역순으로 삽입 — 정렬이 SQL 에서 안 되면 이 순서 그대로 나온다.
		insertTask("mt2", "m2", "feature", "LOAD_ETF_HOLDINGS", "etf_holdings", "DUE",
				"FAILED", null, null, null);
		insertTask("mt1", "m2", "raw", "ETF_HOLDINGS_COLLECTION_KRX", "etf_holdings", "DUE",
				"FULFILLED", "INCOMPLETE", 29L, null);
		insertTask("old", "m1", "raw", "PRICE_COLLECTION_KIS", "price_daily", "DUE",
				"FULFILLED", "VALID", 1L, null);

		List<PipelineStatusRepository.OverviewLane> lanes = repository.overview();

		assertThat(lanes).extracting(PipelineStatusRepository.OverviewLane::pipelineType)
				.containsExactly("etf-daily", "news");
		PipelineStatusRepository.OverviewLane market = lanes.get(0);
		// created_at 최신은 m1(백필)이지만 슬롯 최신은 m2 — 슬롯 축이 이겨야 한다
		assertThat(market.runKey()).isEqualTo("etf-daily:2026-07-27T15:40");
		assertThat(market.tasks()).extracting(PipelineStatusRepository.OverviewTask::taskKey)
				.containsExactly("ETF_HOLDINGS_COLLECTION_KRX", "LOAD_ETF_HOLDINGS"); // raw 먼저
		assertThat(market.tasks().get(0).required()).isTrue();
		// freshness 미배선 상태의 NULL 이 그대로 온다(UNKNOWN 으로 승격되지 않는다).
		assertThat(market.tasks().get(0).freshnessStatus()).isNull();
		// 작업이 안 적힌 뉴스 런도 레인으로 온다 — 부재가 1급 신호다.
		assertThat(lanes.get(1).tasks()).isEmpty();
	}
}
