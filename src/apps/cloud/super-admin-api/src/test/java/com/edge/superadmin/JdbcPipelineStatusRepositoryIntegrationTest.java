package com.edge.superadmin;

import com.edge.superadmin.repository.PipelineStatusRepository;
import com.edge.superadmin.repository.PipelineStatusRepository.PipelineRunStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.TaskStatus;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 원장 조회 SQL 통합 테스트 — 실 `ops_*` 스키마(Testcontainers + Flyway migrations-cloud)를
 * 대상으로 컬럼명·조인·최신행 선택·NULL 매핑이 실제로 맞는지 검증한다(ALPHA-514).
 *
 * <p>손 페이크만으로는 이 조회를 <b>한 줄도 실행하지 않는다</b> — 컬럼명 오타나 조인 실수가
 * 전부 초록으로 통과하고 운영에서야 드러난다(Rule 9: 로직이 바뀌어도 못 깨지는 테스트는 잘못됐다).
 * 원장 테이블의 소유는 data-pipeline 이라 스키마가 남의 손에 바뀔 수 있다는 점이 이 테스트의
 * 값어치다.
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
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, data_status, records_out, failed_records,
				       idempotency_key)
				VALUES (?,?,?,?,?,?,?,?,?,?,?)
				""", id, runId, taskKey, stage, dataset, planStatus, outcome, dataStatus,
				recordsOut, failedRecords, runId + taskKey);
	}

	private void insertAttempt(String id, String taskId, String arn, String finishedAt,
			String startedAt) {
		jdbc.update("""
				INSERT INTO ops_task_attempt (attempt_id, expected_task_id, ecs_task_arn,
				       execution_status, started_at, finished_at)
				VALUES (?,?,?,'SUCCEEDED',?::timestamptz,?::timestamptz)
				""", id, taskId, arn, startedAt, finishedAt);
	}

	@Test
	void 최신_런의_모든_축을_컬럼명_그대로_읽는다() {
		insertRun("r1", "etf-daily:2026-07-27T15:40", "LAUNCHED", "FAILED", "2026-07-27",
				"2026-07-27T06:40:00Z");
		insertTask("t1", "r1", "raw", "PRICE_COLLECTION_KIS", "price_daily", "DUE", "FULFILLED",
				"INCOMPLETE", 2736L, 4L);
		insertAttempt("a1", "t1", "arn:aws:ecs:task/1", "2026-07-27T06:45:00Z",
				"2026-07-27T06:41:00Z");

		PipelineRunStatus run = repository.latestRun().orElseThrow();

		assertThat(run.runKey()).isEqualTo("etf-daily:2026-07-27T15:40");
		assertThat(run.launchStatus()).isEqualTo("LAUNCHED");
		assertThat(run.orchestrationStatus()).isEqualTo("FAILED");
		assertThat(run.tradingDate()).isEqualTo("2026-07-27");
		assertThat(run.tasks()).singleElement().satisfies(t -> {
			assertThat(t.taskKey()).isEqualTo("PRICE_COLLECTION_KIS");
			assertThat(t.stage()).isEqualTo("raw");
			assertThat(t.dataset()).isEqualTo("price_daily");
			assertThat(t.planStatus()).isEqualTo("DUE");
			assertThat(t.outcome()).isEqualTo("FULFILLED");
			// 실행은 성공(FULFILLED)인데 데이터는 불완전하다 — 이 두 축이 함께 와야
			// 화면이 "완료"를 온전한 초록으로 그리지 않는다.
			assertThat(t.dataStatus()).isEqualTo("INCOMPLETE");
			assertThat(t.recordsOut()).isEqualTo(2736L);
			assertThat(t.failedRecords()).isEqualTo(4L);
			assertThat(t.lastFinishedAt()).isNotNull();
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
		assertThat(task.failedRecords()).isNull();
		assertThat(task.lastFinishedAt()).isNull();   // 시도가 없으면 LATERAL 이 NULL
	}

	@Test
	void 시도가_여러_개여도_작업당_한_행이고_마지막_시도를_쓴다() {
		// WHY: 단순 JOIN 이면 작업 행이 시도 수만큼 불어나 25행 화면이 조용히 중복된다.
		insertRun("r3", "etf-daily:2026-07-27T15:42", "LAUNCHED", "SUCCEEDED", null,
				"2026-07-27T08:00:00Z");
		insertTask("t3", "r3", "normalize", "NORMALIZE_NEWS", "news_articles", "DUE", "FULFILLED",
				"VALID", 10L, 0L);
		insertAttempt("a3a", "t3", "arn:aws:ecs:task/3a", "2026-07-27T08:10:00Z",
				"2026-07-27T08:05:00Z");
		insertAttempt("a3b", "t3", "arn:aws:ecs:task/3b", "2026-07-27T08:30:00Z",
				"2026-07-27T08:25:00Z");

		PipelineRunStatus run = repository.latestRun().orElseThrow();

		assertThat(run.tasks()).hasSize(1);
		assertThat(run.tasks().getFirst().lastFinishedAt().toInstant())
				.isEqualTo(java.time.Instant.parse("2026-07-27T08:30:00Z"));
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
		//      빼면 LIMIT 1 이 매 조회마다 다른 행을 골라 **새로고침할 때마다 화면이 바뀐다**.
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
		// `NULLS LAST` 제거는 **결정적으로** 여기서 깨진다 — NULL 이 DESC 에서 맨 앞이라 a5c 의
		// 10:30 이 뽑힌다. `attempt_id DESC` 제거는 동률(a5a·a5b) 중 선택이 임의가 되므로 이
		// 단언이 "언젠가" 깨진다 — 같은 트랜잭션에서 조회를 반복해도 실행계획이 그대로라
		// 비결정성이 재현되지 않으므로, 반복 루프로 확신을 꾸미지 않는다.
		assertThat(run.tasks().getFirst().lastFinishedAt().toInstant())
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
	void 원장이_비어있으면_empty_다() {
		// WHY: 초기 환경은 장애가 아니다. 예외를 던지면 콘솔 페이지가 통째로 안 뜬다.
		jdbc.update("DELETE FROM ops_pipeline_run");

		Optional<PipelineRunStatus> run = repository.latestRun();

		assertThat(run).isEmpty();
	}
}
