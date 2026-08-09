package com.edge.superadmin;

import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.edge.superadmin.repository.ConsoleFactsRepository;
import com.edge.superadmin.repository.ConsoleFactsRepository.ConsoleFacts;
import com.edge.superadmin.repository.ConsoleFactsRepository.OutputRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
import com.edge.superadmin.repository.JdbcConsoleFactsRepository;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 콘솔 사실 조회 SQL 통합 테스트 — 실 스키마(Testcontainers + Flyway migrations-cloud)로
 * 컬럼명·조인·날짜 창·중앙값 표본을 검증한다(ALPHA-738).
 *
 * <p>손 페이크는 이 SQL 을 <b>한 줄도 실행하지 않는다</b>. 여기 걸린 축은 전부 조용히 틀리는
 * 종류다 — 창이 UTC 로 새면 런이 사라지고, 표본에서 산출 0 인 날이 빠지면 중앙값이 올라가
 * 오늘의 급감이 "분포 안"으로 인증되고, 중복 런 행은 엔진에서 식별자 충돌이 되어 규칙 하나가
 * 통째로 <b>못 돎</b> 으로 선다.
 *
 * <p><b>여기서 검증되지 않는 것</b>(Rule 12):
 * <ul>
 *   <li>REPEATABLE READ 스냅샷 보장 — {@link JdbcPipelineStatusRepositoryIntegrationTest} 와 같은
 *       이유로 이 클래스의 {@code @Transactional} 이 먼저 트랜잭션을 열어 안쪽 격리수준이
 *       적용되지 않는다.</li>
 *   <li><b>{@code AT TIME ZONE} 을 통째로 지우는 변이.</b> 시각→날짜 캐스트는 남기면 세션
 *       TimeZone 을 타는데 그 값은 <b>JVM 기본값</b>이라, 로컬(KST)에서는 지워도 같은 답이 나오고
 *       운영(UTC)에서만 하루가 밀린다. 변이 검증으로 확인한 것은 <b>어느 존을 쓰는가</b>까지다
 *       ({@code 'Asia/Seoul'}→{@code 'UTC'} 는 잡힌다). 그래서 이 조회의 날짜 캐스트는 존을 항상
 *       명시해야 한다 — 세션 기본값에 기대는 순간 이 테스트가 못 보는 자리가 된다.</li>
 * </ul>
 */
@Transactional
class JdbcConsoleFactsRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	private static final LocalDate DAY = LocalDate.parse("2026-08-03");

	@Autowired
	private ConsoleFactsRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	private void insertRun(String id, String runKey, String orchestration, String tradingDate,
			String createdAt, String deadline) {
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, orchestration_status, trading_date,
				       hard_deadline_at, created_at, updated_at)
				VALUES (?,?,'etf-daily',?,'LAUNCHED',?,?::date,?::timestamptz,?::timestamptz,
				        ?::timestamptz)
				""", id, runKey, "exec-" + id, orchestration, tradingDate, deadline, createdAt,
				createdAt);
	}

	/** 거래일 하나를 원장에 세운다 — 중앙값 표본은 이 목록으로 잘린다. */
	private void insertTradingDay(String tradingDate) {
		insertRun("r-" + tradingDate, "etf-daily:" + tradingDate + "T15:40", "SUCCEEDED",
				tradingDate, tradingDate + "T06:40:00Z", null);
	}

	private void insertTask(String id, String runId, String taskKey, String dataset,
			String outcome, Long recordsOut, Long failedRecords) {
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, data_status, required,
				       records_out, failed_records, idempotency_key)
				VALUES (?,?,?,'raw',?,'DUE',?,'VALID',true,?,?,?)
				""", id, runId, taskKey, dataset, outcome, recordsOut, failedRecords, id);
	}

	private void insertMissingSlotIssue(String id, String runKey, String status) {
		jdbc.update("""
				INSERT INTO ops_reconciliation_issue (issue_id, issue_type, scope, scope_key,
				       dedupe_key, status)
				VALUES (?,'PLANNER_MISSING','slot',?,?,?)
				""", id, runKey, "planner_missing:" + runKey, status);
	}

	/** 산출 축이 매달릴 ETF 와 번들 — explanation_result·price_movement_trigger 가 이 FK 를 탄다. */
	private void insertEtf() {
		jdbc.update("INSERT INTO entity (entity_id, entity_type, display_name)"
				+ " VALUES ('etf-t601','INSTRUMENT','KODEX 반도체')");
		jdbc.update("INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)"
				+ " VALUES ('etf-t601','XKRX','T601','ETF')");
		jdbc.update("INSERT INTO etf_profile (instrument_id, etf_type) VALUES ('etf-t601','SECTOR')");
		jdbc.update("INSERT INTO release_bundle (bundle_version, component_versions,"
				+ " component_hash, status) VALUES ('v1', '{}'::jsonb, ?, 'DRAFT')", "a".repeat(64));
	}

	/** {@code (etf, trade_date, detected_at)} 이 UNIQUE 라 같은 날 두 트리거는 시각이 달라야 한다. */
	private void insertTrigger(String id, String tradeDate) {
		jdbc.update("""
				INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id,
				       trade_date, detected_at, observed_return, absolute_gate_triggered,
				       relative_gate_triggered, detection_policy_version)
				VALUES (?, 'etf-t601', ?::date, ?::timestamptz, 0.05, true, false, 'p1')
				""", id, tradeDate, kst(tradeDate, detectedMinute++));
	}

	private int detectedMinute;

	/**
	 * 트리거→관측→라우트→런→결과 한 벌. 각 단계가 앞 단계에 UNIQUE 라 결과마다 트리거가 하나이고,
	 * {@code (etf, trade_date, explanation_as_of)} 도 UNIQUE 라 같은 날 두 결과는 시각이 달라야 한다.
	 */
	private void insertResult(String id, String tradeDate, String publicationStatus) {
		String at = kst(tradeDate, detectedMinute);   // 트리거가 쓸 시각과 같은 분
		insertTrigger("trg-" + id, tradeDate);
		jdbc.update("""
				INSERT INTO etf_contribution_observation (contribution_observation_id,
				       price_movement_trigger_id, available_at, data_version)
				VALUES (?,?,?::timestamptz,'d1')
				""", "co-" + id, "trg-" + id, at);
		jdbc.update("""
				INSERT INTO explanation_route (explanation_route_id, contribution_observation_id,
				       route_code, event_search_required, evaluated_at)
				VALUES (?,?,'CONCENTRATED',true,?::timestamptz)
				""", "rt-" + id, "co-" + id, at);
		jdbc.update("""
				INSERT INTO explanation_run (explanation_run_id, explanation_route_id,
				       bundle_version, explanation_as_of, run_status, started_at, finished_at)
				VALUES (?,?,'v1',?::timestamptz,'SUCCEEDED',?::timestamptz,?::timestamptz)
				""", "run-" + id, "rt-" + id, at, at, at);
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id,
				       etf_instrument_id, trade_date, explanation_as_of, explanation_type,
				       summary, publication_status)
				VALUES (?,?,'etf-t601',?::date,?::timestamptz,'EVENT_SUPPORTED','요약',?)
				""", id, "run-" + id, tradeDate, at, publicationStatus);
	}

	private static String kst(String date) {
		return kst(date, 0);
	}

	private static String kst(String date, int minute) {
		return "%sT15:%02d:00+09:00".formatted(date, 40 + minute);
	}

	private OutputRow output(ConsoleFacts f, String id) {
		return f.outputs().stream().filter(o -> o.id().equals(id)).findFirst().orElseThrow();
	}

	@Test
	void 런_축을_원장_컬럼_그대로_읽는다() {
		insertRun("r1", "etf-daily:2026-08-03T15:40", "RUNNING", "2026-08-03",
				"2026-08-03T06:40:00Z", "2026-08-03T12:40:00Z");

		ConsoleFacts f = repository.facts(DAY);

		assertThat(f.today()).isEqualTo(DAY);
		assertThat(f.dbNow()).isNotNull();
		assertThat(f.runs()).singleElement().satisfies(r -> {
			assertThat(r.runKey()).isEqualTo("etf-daily:2026-08-03T15:40");
			assertThat(r.lane()).isEqualTo("etf-daily");
			assertThat(r.tradingDate()).isEqualTo(DAY);
			assertThat(r.ledgerStatus()).isEqualTo("RUNNING");
			assertThat(r.deadline()).isNotNull();
			assertThat(r.ledgerUpdated()).isNotNull();
			// 실재 런에는 "계획된 슬롯인가" 계측이 없다 — false 로 메우면 모름이 단정이 된다.
			assertThat(r.planned()).isNull();
			assertThat(r.noRunRow()).isNull();
		});
	}

	/**
	 * 거래일이 NULL 인 런(비거래일)은 거래일 창으로는 절대 안 잡힌다 — 계획 시각의 KST 날짜로
	 * 주워야 그날 실패한 런이 화면에서 사라지지 않는다.
	 */
	@Test
	void 비거래일_런은_계획_시각의_KST_날짜로_잡힌다() {
		// 08-02 16:00Z 는 KST 로만 08-03 이다 — 창이 UTC 로 새면 이 런이 어제로 빠진다.
		insertRun("r1", "etf-daily:2026-08-03T01:00", "FAILED", null,
				"2026-08-02T16:00:00Z", null);

		assertThat(repository.facts(DAY).runs()).extracting(RunRow::runKey)
				.containsExactly("etf-daily:2026-08-03T01:00");
		assertThat(repository.facts(DAY.minusDays(1)).runs()).isEmpty();
	}

	@Test
	void 런_행이_없는_계획_슬롯은_런처럼_생긴_행으로_나간다() {
		insertMissingSlotIssue("i1", "etf-daily:2026-08-03T15:40", "OPEN");

		assertThat(repository.facts(DAY).runs()).singleElement().satisfies(r -> {
			assertThat(r.runKey()).isEqualTo("etf-daily:2026-08-03T15:40");
			assertThat(r.lane()).isEqualTo("etf-daily");
			assertThat(r.tradingDate()).isEqualTo(DAY);
			assertThat(r.planned()).isTrue();
			assertThat(r.noRunRow()).isTrue();
			assertThat(r.ledgerStatus()).isNull();
		});
	}

	/**
	 * 이슈가 아직 OPEN 인 채 런이 생기면 같은 run_key 가 두 행으로 나간다 — 엔진은 그걸
	 * <b>식별자 충돌</b>로 읽어 R01 을 통째로 못 돎 으로 세운다. status 만 믿으면 그 창이 열린다.
	 */
	@Test
	void 런이_생긴_뒤_안_닫힌_이슈는_유령_행을_만들지_않는다() {
		insertRun("r1", "etf-daily:2026-08-03T15:40", "SUCCEEDED", "2026-08-03",
				"2026-08-03T06:40:00Z", null);
		insertMissingSlotIssue("i1", "etf-daily:2026-08-03T15:40", "OPEN");

		assertThat(repository.facts(DAY).runs()).extracting(RunRow::runKey)
				.containsExactly("etf-daily:2026-08-03T15:40");
	}

	/**
	 * 슬롯 키를 못 읽으면 레인·거래일이 null 이다 — 잘못 자른 조각을 레인 이름이라고 우기면
	 * 화면이 존재하지 않는 레인을 그린다. 사건 축(run_key)은 그대로 남아 R01 은 여전히 선다.
	 */
	@Test
	void 형식이_깨진_슬롯_키는_레인을_지어내지_않고_경고를_남긴다() {
		insertMissingSlotIssue("i1", ":2026-08-03T15:40", "OPEN");
		ListAppender<ILoggingEvent> logs = captureWarnings(JdbcConsoleFactsRepository.class);

		assertThat(repository.facts(DAY).runs()).singleElement().satisfies(r -> {
			assertThat(r.runKey()).isEqualTo(":2026-08-03T15:40");
			assertThat(r.lane()).isNull();
			assertThat(r.tradingDate()).isNull();
			assertThat(r.noRunRow()).isTrue();
		});
		/* null 을 내는 것만으로는 Rule 12 를 못 만족한다 — 응답에는 "레인 미상"으로만 보여
		 * Planner 키 형식이 갈렸다는 사실이 아무 데도 안 남는다. 경고가 그 유일한 장치라면
		 * 경고의 존재가 곧 계약이다(경고를 지우는 변이가 여기서 잡혀야 한다). */
		assertThat(logs.list).extracting(ILoggingEvent::getFormattedMessage)
				.anySatisfy(m -> assertThat(m).contains("슬롯 키를 못 읽었다"));
	}

	private ch.qos.logback.classic.Logger capturedLogger;
	private ListAppender<ILoggingEvent> capturedAppender;

	/** 지정 클래스의 WARN 을 담는다. logback 로거는 전역이라 <b>반드시 떼야</b> 다음 테스트로 안 샌다. */
	private ListAppender<ILoggingEvent> captureWarnings(Class<?> type) {
		capturedAppender = new ListAppender<>();
		capturedAppender.start();
		capturedLogger = (ch.qos.logback.classic.Logger) LoggerFactory.getLogger(type);
		capturedLogger.addAppender(capturedAppender);
		return capturedAppender;
	}

	@org.junit.jupiter.api.AfterEach
	void detachAppender() {
		if (capturedLogger != null) {
			capturedLogger.detachAppender(capturedAppender);
			capturedLogger = null;
		}
	}

	@Test
	void 해소된_이슈와_다른_날_슬롯은_안_담는다() {
		insertMissingSlotIssue("i1", "etf-daily:2026-08-03T15:40", "RESOLVED");
		insertMissingSlotIssue("i2", "etf-daily:2026-08-02T15:40", "OPEN");

		assertThat(repository.facts(DAY).runs()).isEmpty();
	}

	@Test
	void 작업_축은_완전성_jsonb_와_시도_수를_함께_낸다() {
		insertRun("r1", "etf-daily:2026-08-03T15:40", "RUNNING", "2026-08-03",
				"2026-08-03T06:40:00Z", null);
		insertTask("t1", "r1", "ETF_HOLDINGS_COLLECTION_KRX", "etf_holdings", "FULFILLED",
				906L, 0L);
		insertTask("t2", "r1", "PRICE_COLLECTION_KIS", "price_daily", "PENDING", null, null);
		jdbc.update("UPDATE ops_expected_task SET completeness = ?::jsonb,"
				+ " dataset_contract_key = 'ETF_HOLDINGS_KRX_EOD', dataset_contract_version = 'v1',"
				+ " dataset_contract_snapshot = '{}'::jsonb, freshness_status = 'UNKNOWN',"
				+ " freshness_reason = 'ACTUAL_AS_OF_UNVERIFIED', expected_as_of_date = ?::date"
				+ " WHERE expected_task_id = 't1'",
				"{\"expected\":33,\"received\":30,\"missing\":3}", "2026-08-03");
		jdbc.update("""
				INSERT INTO ops_task_attempt (attempt_id, expected_task_id, ecs_task_arn,
				       execution_status)
				VALUES ('a1','t1','arn:aws:ecs:task/1','FAILED'),
				       ('a2','t1','arn:aws:ecs:task/2','SUCCEEDED')
				""");

		ConsoleFacts f = repository.facts(DAY);

		assertThat(f.tasks()).hasSize(2);
		assertThat(f.tasks().get(0)).satisfies(t -> {
			assertThat(t.taskKey()).isEqualTo("ETF_HOLDINGS_COLLECTION_KRX");
			assertThat(t.runKey()).isEqualTo("etf-daily:2026-08-03T15:40");
			assertThat(t.pipelineType()).isEqualTo("etf-daily");
			assertThat(t.tradingDate()).isEqualTo(DAY);
			assertThat(t.completenessExpected()).isEqualTo(33L);
			assertThat(t.completenessReceived()).isEqualTo(30L);
			assertThat(t.completenessMissing()).isEqualTo(3L);
			assertThat(t.attempts()).isEqualTo(2L);
			assertThat(t.datasetContractKey()).isEqualTo("ETF_HOLDINGS_KRX_EOD");
			assertThat(t.expectedAsOf()).isEqualTo(DAY);
			assertThat(t.freshnessReason()).isEqualTo("ACTUAL_AS_OF_UNVERIFIED");
			assertThat(t.actualAsOf()).isNull();
		});
		assertThat(f.tasks().get(1)).satisfies(t -> {
			// 0 으로 뭉개면 "0건 처리"와 "신호 없음"이 화면에서 같은 칸이 된다.
			assertThat(t.recordsOut()).isNull();
			assertThat(t.failedRecords()).isNull();
			assertThat(t.completenessExpected()).isNull();
			assertThat(t.attempts()).isZero();
		});
	}

	/**
	 * 기준(중앙값)의 표본은 <b>거래일 목록</b>이다. "값이 있는 날"로 세면 산출 0 이었던 거래일이
	 * 빠져 중앙값이 올라간다 — 여기서는 [0,0,1,1]→0.5 가 [1,1]→1.0 으로 뒤집힌다.
	 *
	 * <p>오늘 값은 같은 종의 트리거 2행이다 — {@code DISTINCT} 가 빠지면 1 이 2 가 된다(단위가 '종').
	 */
	@Test
	void 중앙값_표본은_거래일_목록이고_산출_0_인_날을_버리지_않는다() {
		insertEtf();
		insertTradingDay("2026-07-29");
		insertTradingDay("2026-07-30");
		insertTradingDay("2026-07-31");
		insertTradingDay("2026-08-01");
		insertTradingDay("2026-08-03");
		insertTrigger("trg-1", "2026-07-30");
		insertTrigger("trg-2", "2026-08-01");
		insertTrigger("trg-3", "2026-08-03");
		insertTrigger("trg-4", "2026-08-03");

		assertThat(output(repository.facts(DAY), "o.trig")).satisfies(trig -> {
			assertThat(trig.today()).isEqualTo(1L);
			assertThat(trig.base()).isEqualTo(0.5d);
		});
	}

	@Test
	void 기준을_만들_거래일이_없으면_base_는_null_이다() {
		insertEtf();
		insertTradingDay("2026-08-03");
		insertResult("res-a", "2026-08-03", "PUBLISHED");

		assertThat(output(repository.facts(DAY), "o.pub")).satisfies(pub -> {
			assertThat(pub.today()).isEqualTo(1L);
			// 0 이 아니다 — 0 으로 채우면 나눗셈이 성립하는 척해서 거짓 편차가 선다.
			assertThat(pub.base()).isNull();
		});
	}

	/**
	 * 🔴 <b>{@code trading_date} 는 거래일 달력이 아니다</b> — Planner 는 휴장일에도 그 컬럼을
	 * 채우고(`plan_slot` 이 `is_trading_day` 와 무관하게 쓴다), 휴장 판정은 기대 작업의
	 * {@code skip_reason='NON_TRADING_DAY'} 에만 남는다. 안 빼면 휴장일의 산출 0 이 표본에 들어가
	 * <b>중앙값이 내려가고 R13 이 둔해진다</b> — 진짜 급감을 놓치는 방향이다.
	 *
	 * <p>표본 [2] → 2.0. 휴장일을 안 빼면 [0, 2] → 1.0 이라 이 단언이 갈린다.
	 */
	@Test
	void 휴장일은_중앙값_표본에서_빠진다() {
		insertTradingDay("2026-08-01");
		insertTradingDay("2026-08-02");
		insertTradingDay("2026-08-03");
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       plan_status, skip_reason, required, idempotency_key)
				VALUES ('t-hol','r-2026-08-02','PRICE_COLLECTION_KIS','raw','SKIPPED',
				        'NON_TRADING_DAY',true,'t-hol')
				""");
		/* 🔴 휴장 신호는 KR 시장 레인에만 붙는다 — 뉴스 레인은 휴장일에도 돈다. 제외를 **런 단위**로
		 * 상관시키면 이 런이 같은 날짜를 표본에 다시 넣는다(4라운드가 잡은 모양). */
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, orchestration_status, trading_date, created_at)
				VALUES ('r-news','news:2026-08-02T15:30','news','exec-r-news','LAUNCHED',
				        'SUCCEEDED','2026-08-02'::date,'2026-08-02T06:30:00Z'::timestamptz)
				""");
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, available_at)
				VALUES ('doc-1','NEWS','BIGKINDS','n1','기사','2026-08-01T06:00:00Z'::timestamptz),
				       ('doc-2','NEWS','BIGKINDS','n2','기사','2026-08-01T07:00:00Z'::timestamptz)
				""");

		assertThat(output(repository.facts(DAY), "o.doc").base()).isEqualTo(2.0d);
	}

	/**
	 * 🔴 Planner 가 통째로 실패한 날은 {@code ops_pipeline_run} 에 한 행도 없다. 최신 날짜를 런
	 * 원장에서만 고르면 기본 조회가 <b>어제</b>를 보고, 그날의 R01 P0 가 화면에서 통째로 사라진다 —
	 * 하필 콘솔이 가장 시끄러워야 하는 날에.
	 */
	@Test
	void 런이_0건이고_계획_결손만_있는_날도_기본_조회가_본다() {
		insertTradingDay("2026-08-01");
		insertMissingSlotIssue("i1", "etf-daily:2026-08-03T15:40", "OPEN");

		ConsoleFacts f = repository.facts(null);

		assertThat(f.today()).isEqualTo(DAY);
		assertThat(f.runs()).singleElement().satisfies(r -> {
			assertThat(r.runKey()).isEqualTo("etf-daily:2026-08-03T15:40");
			assertThat(r.noRunRow()).isTrue();
		});
	}

	/**
	 * 🔴 Planner 통째 실패일의 실측 0 도 표본이다. 빼면 기준이 올라가 <b>위쪽 이상이 조용해진다</b> —
	 * "빼는 쪽이 과민이라 안전하다"는 논거는 R13 이 ±25% 양방향이라 성립하지 않는다.
	 *
	 * <p>표본 [0, 2] → 1.0. 계획 결손일을 빼면 [2] → 2.0 이라 이 단언이 갈린다.
	 */
	@Test
	void 런_없이_계획_결손만_있던_날도_중앙값_표본이다() {
		insertTradingDay("2026-08-01");
		insertTradingDay("2026-08-03");
		insertMissingSlotIssue("i1", "etf-daily:2026-07-31T15:40", "OPEN");
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, available_at)
				VALUES ('doc-1','NEWS','BIGKINDS','n1','기사','2026-08-01T06:00:00Z'::timestamptz),
				       ('doc-2','NEWS','BIGKINDS','n2','기사','2026-08-01T07:00:00Z'::timestamptz)
				""");

		assertThat(output(repository.facts(DAY), "o.doc").base()).isEqualTo(1.0d);
	}

	/**
	 * 🔴 조회 창은 거래일이 NULL 인 런을 계획 시각의 KST 날짜로 줍는데, <b>최신 날짜</b>가 그 축을
	 * 안 보면 그 런은 창에 들어올 기회조차 없다 — 어제가 선택되고 오늘 실패한 런이 기본 화면에서
	 * 사라진다. "이 런은 어느 날의 것인가"는 한 식이어야 한다.
	 */
	@Test
	void 거래일이_NULL_인_런도_기본_조회의_날짜를_정한다() {
		insertTradingDay("2026-08-02");
		// 08-02 16:00Z 는 KST 로만 08-03 이다.
		insertRun("r-null", "etf-daily:2026-08-03T01:00", "FAILED", null,
				"2026-08-02T16:00:00Z", null);

		ConsoleFacts f = repository.facts(null);

		assertThat(f.today()).isEqualTo(DAY);
		assertThat(f.runs()).extracting(RunRow::runKey)
				.containsExactly("etf-daily:2026-08-03T01:00");
	}

	/**
	 * 🔴 휴장일 제외와 계획 결손일 합집합이 <b>따로</b> 서면 후자가 전자가 뺀 날을 다시 넣는다 —
	 * 휴장일에도 뉴스 레인은 돌고 결손 슬롯은 레인별로 열린다. 제외는 합집합 뒤 한 번이어야 한다.
	 *
	 * <p>표본 [2] → 2.0. 계획 결손일이 휴장일을 되살리면 [0, 2] → 1.0 이라 이 단언이 갈린다.
	 */
	@Test
	void 계획_결손일이_휴장일을_표본에_되살리지_않는다() {
		insertTradingDay("2026-08-01");
		insertTradingDay("2026-08-02");
		insertTradingDay("2026-08-03");
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       plan_status, skip_reason, required, idempotency_key)
				VALUES ('t-hol','r-2026-08-02','PRICE_COLLECTION_KIS','raw','SKIPPED',
				        'NON_TRADING_DAY',true,'t-hol')
				""");
		// 같은 휴장일에 뉴스 레인의 결손 슬롯이 남아 있다.
		insertMissingSlotIssue("i1", "news:2026-08-02T15:30", "OPEN");
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, available_at)
				VALUES ('doc-1','NEWS','BIGKINDS','n1','기사','2026-08-01T06:00:00Z'::timestamptz),
				       ('doc-2','NEWS','BIGKINDS','n2','기사','2026-08-01T07:00:00Z'::timestamptz)
				""");

		assertThat(output(repository.facts(DAY), "o.doc").base()).isEqualTo(2.0d);
	}

	/**
	 * 🔴 <b>자르는 것은 휴장일을 뺀 뒤여야 한다.</b> 먼저 10개로 자르면 그중 하나가 휴장일일 때
	 * 표본이 9개로 줄고 <b>11번째 거래일이 영영 안 들어온다</b> — 표본 수가 바뀌면 중앙값도 바뀐다.
	 *
	 * <p>거래일 12개 중 위에서 세 번째(07-29)가 휴장. 최근 5거래일에 문서 1건씩, 그 아래는 0건.
	 * <ul>
	 *   <li>제대로: 휴장 뺀 뒤 10개 → [1×5, 0×5] → 짝수라 (0+1)/2 = <b>0.5</b></li>
	 *   <li>먼저 자르면: 9개(07-17 유실) → [1×5, 0×4] → 홀수라 가운데가 <b>1.0</b></li>
	 * </ul>
	 */
	@Test
	void 휴장일을_뺀_뒤에_열_개로_자른다() {
		String[] tradingDays = {"2026-07-16", "2026-07-17", "2026-07-20", "2026-07-21",
				"2026-07-22", "2026-07-23", "2026-07-24", "2026-07-27", "2026-07-28",
				"2026-07-29", "2026-07-30", "2026-07-31"};
		for (String d : tradingDays) {
			insertTradingDay(d);
		}
		insertTradingDay("2026-08-03");
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       plan_status, skip_reason, required, idempotency_key)
				VALUES ('t-hol','r-2026-07-29','PRICE_COLLECTION_KIS','raw','SKIPPED',
				        'NON_TRADING_DAY',true,'t-hol')
				""");
		// 최근 5거래일(휴장 제외)에만 문서 1건씩 — 07-31·07-30·07-28·07-27·07-24.
		int n = 0;
		for (String d : new String[] {"2026-07-31", "2026-07-30", "2026-07-28", "2026-07-27",
				"2026-07-24"}) {
			jdbc.update("""
					INSERT INTO document (document_id, document_type, source_code,
					       source_document_id, title, available_at)
					VALUES (?,'NEWS','BIGKINDS',?,'기사',?::timestamptz)
					""", "doc-" + n, "n-" + n, d + "T09:00:00+09:00");
			n++;
		}

		assertThat(output(repository.facts(DAY), "o.doc").base()).isEqualTo(0.5d);
	}

	/** 미래 런 한 건이 기본 조회를 오지 않은 날로 옮기면 그 화면의 산출은 전부 0 이다. */
	@Test
	void 미래_거래일_런은_기본_조회의_날짜가_되지_않는다() {
		insertTradingDay("2026-08-03");
		insertTradingDay("2099-01-01");

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/**
	 * 미래 슬롯 키가 하나라도 있으면 기본 조회가 오지 않은 날로 뛴다 — 그 화면의 산출은 전부 0 이라
	 * R13 이 다섯 산출을 −100% 로 판정한다(요청 파라미터의 미래 날짜를 400 으로 막은 것과 같은 이유,
	 * 이쪽은 파라미터가 아니라 원장에서 온다).
	 */
	@Test
	void 미래_슬롯_키는_기본_조회의_날짜가_되지_않는다() {
		insertTradingDay("2026-08-03");
		insertMissingSlotIssue("i1", "etf-daily:2099-01-01T15:40", "OPEN");

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/** 달력에 없는 날짜가 든 슬롯 키 하나가 조회 전체를 죽이면 콘솔이 통째로 안 뜬다. */
	@Test
	void 달력에_없는_슬롯_날짜는_건너뛰고_조회를_죽이지_않는다() {
		insertTradingDay("2026-08-01");
		insertMissingSlotIssue("i1", "etf-daily:2026-02-31T15:40", "OPEN");

		assertThat(repository.facts(null).today()).isEqualTo(LocalDate.parse("2026-08-01"));
	}

	@Test
	void 뉴스_문서_산출은_수집_시각의_KST_날짜로_세고_공시는_안_센다() {
		insertTradingDay("2026-08-03");
		// 08-02 16:00Z = 08-03 01:00 KST — UTC 로 세면 이 문서가 어제로 새어 나간다.
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, available_at)
				VALUES ('doc-1','NEWS','BIGKINDS','n1','기사','2026-08-02T16:00:00Z'::timestamptz),
				       ('doc-2','DISCLOSURE','DART','r1','공시','2026-08-02T16:00:00Z'::timestamptz)
				""");

		assertThat(output(repository.facts(DAY), "o.doc").today()).isEqualTo(1L);
	}

	/**
	 * 게시·발번 경계의 두 방향. 정상 조합(res-c)을 같이 두는 것이 요점이다 — 없으면 "전부 센다"
	 * 로 바꿔도 통과한다.
	 *
	 * <p>INVALIDATION 행은 {@code explanation_result_id} 가 NULL 이라(ADR-0044 2형상) 어느 쪽에도
	 * 안 걸려야 한다. 조인을 느슨하게 잡으면 여기서 수가 늘어난다.
	 */
	@Test
	void 경계_정합은_두_방향을_각각_세고_INVALIDATION_은_발번으로_안_친다() {
		insertEtf();
		insertTradingDay("2026-08-03");
		insertResult("res-a", "2026-08-03", "PUBLISHED");   // 발번 행 없음 — 미발번 1건
		insertResult("res-b", "2026-08-03", "WITHDRAWN");   // 발번됐는데 지금 비게시 — 1건
		insertResult("res-c", "2026-08-03", "PUBLISHED");   // 정상 — 어느 쪽에도 안 걸린다
		long tenantId = jdbc.queryForObject("""
				INSERT INTO tenant (tenant_name, environment, status)
				VALUES ('t','DEV','ACTIVE') RETURNING tenant_id
				""", Long.class);
		jdbc.update("""
				INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type,
				       explanation_result_id, target_explanation_result_id, reason)
				VALUES (?, 1, 'NEW', 'res-b', NULL, NULL),
				       (?, 2, 'NEW', 'res-c', NULL, NULL),
				       (?, 3, 'INVALIDATION', NULL, 'res-b', '오탐지')
				""", tenantId, tenantId, tenantId);

		assertThat(repository.facts(DAY).boundary()).satisfies(b -> {
			assertThat(b.publishedWithoutDelivery()).isEqualTo(1L);   // res-a 만
			assertThat(b.deliveryNowNonpublished()).isEqualTo(1L);    // res-b 만
			assertThat(b.deliveryRows()).isEqualTo(3L);
		});
	}

	@Test
	void 날짜를_생략하면_원장이_아는_가장_최근_날을_본다() {
		insertTradingDay("2026-08-01");
		insertTradingDay("2026-08-03");

		ConsoleFacts f = repository.facts(null);

		assertThat(f.today()).isEqualTo(DAY);
		assertThat(f.runs()).extracting(RunRow::runKey)
				.containsExactly("etf-daily:2026-08-03T15:40");
	}
}
