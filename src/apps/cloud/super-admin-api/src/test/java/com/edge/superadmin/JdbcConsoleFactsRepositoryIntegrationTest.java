package com.edge.superadmin;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.edge.superadmin.repository.ConsoleFactsRepository;
import com.edge.superadmin.repository.ConsoleFactsRepository.OutputRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.TaskRow;
import com.edge.superadmin.repository.JdbcConsoleFactsRepository;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 콘솔 사실 조회의 <b>조회 창 + 런 축(계획 결손 슬롯 포함) + 작업 축 + 산출 축</b> 통합 테스트 — 실 스키마(Testcontainers + Flyway
 * migrations-cloud)로 컬럼명·날짜 창·정렬을 검증한다(ALPHA-738).
 *
 * <p>손 페이크는 이 SQL 을 <b>한 줄도 실행하지 않는다</b>. 여기 걸린 축은 조용히 틀리는 종류다 —
 * 창이 UTC 로 새면 하루가 밀리고, 미래 슬롯 키 하나가 기본 조회를 오지 않은 날로 옮기면 그 화면은
 * 전부 0 이 된다.
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

	/** {@code price_movement_trigger} 의 UNIQUE 를 피하려 시각을 갈라 주는 카운터. */
	private int triggerSeq;

	/** {@code explanation_result} 의 게시 grain UNIQUE 를 피하려 as-of 를 갈라 주는 카운터. */
	private int publishedSeq;

	@Autowired
	private ConsoleFactsRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	/**
	 * ⚠️ {@code pipelineType}·{@code updatedAt} 을 <b>따로 받는다</b>. 한때 레인을
	 * {@code 'etf-daily'} 로 박고 {@code updated_at} 에 {@code created_at} 을 그대로 넣었는데,
	 * 그러면 <b>어떤 단언으로도</b> 그 두 컬럼을 못 잰다 — SQL 이 엉뚱한 컬럼을 읽어도(레인을
	 * {@code schedule_slot} 에서, 갱신 시각을 {@code created_at} 에서) 값이 같아 통과한다.
	 * 픽스처가 컬럼을 못 가르면 그 컬럼은 계약이 아니다.
	 */
	private void insertRun(String id, String runKey, String pipelineType, String orchestration,
			String tradingDate, String createdAt, String updatedAt, String deadline) {
		jdbc.update("""
				INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, pipeline_type,
				       execution_name, launch_status, orchestration_status, trading_date,
				       hard_deadline_at, created_at, updated_at)
				VALUES (?,?,?,?,'LAUNCHED',?,?::date,?::timestamptz,?::timestamptz,
				        ?::timestamptz)
				""", id, runKey, pipelineType, "exec-" + id, orchestration, tradingDate, deadline,
				createdAt, updatedAt);
	}

	/** 거래일 하나를 원장에 세운다. */
	private void insertTradingDay(String tradingDate) {
		insertRun("r-" + tradingDate, "etf-daily:" + tradingDate + "T15:40", "etf-daily",
				"SUCCEEDED", tradingDate, tradingDate + "T06:40:00Z", tradingDate + "T06:40:00Z",
				null);
	}

	private void insertTask(String id, String runId, String taskKey, String stage, String dataset,
			String outcome, Long recordsOut, Long failedRecords, String completeness) {
		insertTask(id, runId, taskKey, stage, dataset, outcome, true, recordsOut, failedRecords,
				completeness);
	}

	/**
	 * ⚠️ {@code required} 를 인자로 받는다 — 항상 {@code true} 로 넣으면 그 컬럼을 상수로 바꾸는
	 * 변이가 통과한다(픽스처가 컬럼을 못 가르면 그 컬럼은 계약이 아니다).
	 *
	 * <p>{@code data_status} 는 귀결에 맞춘다 — 프로듀서는 PENDING 일 때 {@code UNKNOWN} 을 쓴다
	 * ({@code ops/ledger.py}). 원장에 안 나오는 조합을 픽스처가 만들지 않게.
	 */
	private void insertTask(String id, String runId, String taskKey, String stage, String dataset,
			String outcome, boolean required, Long recordsOut, Long failedRecords,
			String completeness) {
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, task_outcome, data_status, required,
				       records_out, failed_records, completeness, idempotency_key)
				VALUES (?,?,?,?,?,'DUE',?,?,?,?,?,?::jsonb,?)
				""", id, runId, taskKey, stage, dataset, outcome,
				"PENDING".equals(outcome) ? "UNKNOWN" : "VALID", required, recordsOut,
				failedRecords, completeness, id);
	}

	/**
	 * 그 날을 <b>휴장일</b>로 만든다 — 어느 런이든 KR 시장 작업 하나가 {@code NON_TRADING_DAY} 로
	 * 건너뛰어졌으면 휴장이다({@code ops/planner.py}).
	 *
	 * <p>⚠️ 신호는 <b>기대 작업</b>에 붙지 런에 붙지 않는다. 그래서 휴장일에도 도는 레인(뉴스·공시)의
	 * 런은 그 날짜에 그대로 남는다 — 제외를 <b>런 단위</b>로 하면 그 런이 같은 날짜를 표본에 되살린다.
	 */
	private void insertHoliday(String tradingDate) {
		insertRun("r-hol-" + tradingDate, "etf-daily:" + tradingDate + "T15:40", "etf-daily",
				"SUCCEEDED", tradingDate, tradingDate + "T06:40:00Z", tradingDate + "T06:40:00Z",
				null);
		/* ⚠️ `SKIPPED` 면 `task_outcome`·`data_status` 는 **NULL** 이다 — 스키마 주석과
		 * `ops/ledger.py` 가 같은 말을 한다(축 분리). 값을 채우면 원장에 없는 조합이 된다. */
		jdbc.update("""
				INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key, stage,
				       dataset, plan_status, required, skip_reason, idempotency_key)
				VALUES (?,?,?,'raw','price','SKIPPED',true,'NON_TRADING_DAY',?)
				""", "t-hol-" + tradingDate, "r-hol-" + tradingDate, "PRICE_COLLECTION_KIS",
				"t-hol-" + tradingDate);
	}

	/**
	 * {@code available_at} 축 산출({@code o.doc})이 세는 문서 한 건.
	 *
	 * <p>{@code availableAt} 을 <b>오프셋 있는 문자열로 그대로 받는다</b> — KST/UTC 경계에 걸린
	 * 시각을 픽스처가 직접 지정해야 존을 바꾸는 변이가 잡힌다.
	 */
	private void insertDocument(String id, String type, String availableAt) {
		/* ⚠️ {@code published_at} 을 **다른 날**로 둔다 — 두 시각이 같으면 산출이 `available_at`
		 * 대신 `published_at` 을 읽는 변이가 통과한다(리뷰가 잡았다).
		 *
		 * ⚠️ **원장에서 둘이 늘 다르지는 않다** — `steps/load_documents.py` 는
		 * `available_at = fetched_at or published_at` 로 폴백해서 같은 행이 실제로 생긴다.
		 * 여기서 가르는 것은 **그 컬럼을 읽는지 재기 위한 것**이지 원장 불변식이 아니다. */
		jdbc.update("""
				INSERT INTO document (document_id, document_type, source_code, source_document_id,
				       title, published_at, available_at)
				VALUES (?,?,'BIGKINDS',?,?,?::timestamptz,?::timestamptz)
				""", id, type, "src-" + id, "제목 " + id, "2026-07-01T09:00:00+09:00", availableAt);
	}

	/**
	 * 문서에 매달린 주장 하나 — {@code o.asr} 이 세는 것. {@code available_at} 은 문서와 <b>따로</b>
	 * 받는다(그 축이 문서가 아니라 주장 자신의 컬럼이다).
	 */
	private void insertAssertion(String id, String documentId, String availableAt) {
		jdbc.update("""
				INSERT INTO document_assertion (assertion_id, document_id, event_type_code,
				       predicate_code, modality_code, available_at)
				VALUES (?,?,?,'P','REPORTED',?::timestamptz)
				""", id, documentId,
				// 같은 문서에 주장 둘을 달려면 유형이 달라야 한다(스키마의 UNIQUE).
				"ET-" + id, availableAt);
	}

	/** 소스 이벤트 하나 — {@code o.evt} 가 세는 것. */
	private void insertSourceEvent(String id, String sourceClass, String availableAt) {
		jdbc.update("""
				INSERT INTO source_event (source_event_id, source_class, event_type_code,
				       available_at)
				VALUES (?,?,'ET',?::timestamptz)
				""", id, sourceClass, availableAt);
	}

	/**
	 * 그 날 그 ETF 의 <b>게시된</b> 설명 한 건 — {@code o.pub} 이 세는 것.
	 *
	 * <p>{@code o.trig} 로 대신 못 잰다: {@code o.pub} 에는 그쪽에 없는
	 * <b>{@code publication_status='PUBLISHED'} 술어</b>가 있다(리뷰가 잡았다 — "같은 계약"이라는
	 * 내 정당화가 틀렸다). 그래서 사슬을 세운다:
	 * {@code price_movement_trigger → etf_contribution_observation → explanation_route →
	 * explanation_run → explanation_result}.
	 */
	private void insertPublished(String id, String tradingDate, String etf, String status) {
		insertTrigger("trg-" + id, tradingDate, etf);
		/* ⚠️ `uq_explanation_result_published_grain` 이 (종목, 거래일, as-of) 를 묶는다 — 같은
		 * 종목·날짜의 게시 둘을 넣으려면 as-of 가 달라야 한다(재설명이 실제로 그 형태다).
		 * 그래야 `count(DISTINCT etf_instrument_id)` 계약을 잴 수 있다. */
		String at = "%sT15:%02d:00+09:00".formatted(tradingDate, publishedSeq++ % 60);
		jdbc.update("""
				INSERT INTO release_bundle (bundle_version, component_versions, component_hash, status)
				VALUES ('v1', '{"engine":"1"}'::jsonb, ?, 'DRAFT')
				ON CONFLICT (bundle_version) DO NOTHING
				""", "a".repeat(64));
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
				""", "run-" + id, "rt-" + id, at, at, at);   // SUCCEEDED 는 finished_at 을 요구한다
		jdbc.update("""
				INSERT INTO explanation_result (explanation_result_id, explanation_run_id,
				       etf_instrument_id, trade_date, explanation_as_of, explanation_type,
				       summary, publication_status)
				VALUES (?,?,?,?::date,?::timestamptz,'EVENT_SUPPORTED','요약',?)
				""", id, "run-" + id, etf, tradingDate, at, status);
	}

	/**
	 * 그 날 그 ETF 의 배치 트리거 한 건 — {@code o.trig} 가 세는 것.
	 *
	 * <p>표본·창 계약을 재는 대부분의 테스트가 이 테이블을 쓴다 — {@code trade_date} 축이고
	 * {@code marketBound} 이면서 사슬이 짧기 때문이다. ⚠️ <b>{@code o.pub} 을 이걸로 대신 재지는
	 * 못한다</b>: 그쪽에는 여기 없는 {@code publication_status} 술어가 있어
	 * {@link #insertPublished} 가 따로 있다(리뷰가 잡은 자리 — "같은 계약"이라 적었던 것이 틀렸다).
	 *
	 * <p>⚠️ 여기도 FK 가 없지는 않다 — {@code ALTER TABLE} 로 {@code etf_profile} 을 물고 있어
	 * 아래처럼 종목 사슬 셋을 먼저 세운다.
	 *
	 * <p>{@code etf} 를 인자로 받는 이유는 그 산출이 {@code count(DISTINCT etf_instrument_id)} 라서다
	 * — 같은 ETF 를 두 번 넣어도 1 이어야 하고, 그 계약은 값을 갈라야만 재진다.
	 * {@code detected_at} 도 갈라 둔다({@code (etf,trade_date,detected_at)} 이 UNIQUE 다).
	 */
	/** 테넌트 하나. {@code tenant_id} 는 IDENTITY 라 이름으로 되찾는다. */
	private long insertTenant(String name) {
		return insertTenant(name, "DEV", "ACTIVE");
	}

	/**
	 * ⚠️ {@code environment}·{@code status} 를 <b>따로 받는다</b>. 전건을 DEV·ACTIVE 로 박으면
	 * 테넌트 가드를 {@code status='ACTIVE'} 나 DEV 한정으로 좁히는 변이가 통과한다 —
	 * {@code _fanout_new} 는 <b>필터 없이 tenant 전건</b>에 발번하므로, 그런 환경에서 미발번
	 * 게시본이 0 으로 숨는다(리뷰가 잡았다).
	 */
	private long insertTenant(String name, String environment, String status) {
		jdbc.update("INSERT INTO tenant (tenant_name, environment, status) VALUES (?,?,?)",
				name, environment, status);
		return jdbc.queryForObject("SELECT tenant_id FROM tenant WHERE tenant_name = ?", Long.class,
				name);
	}

	/**
	 * 그 테넌트에게 그 결과를 <b>발번</b>한다(NEW).
	 *
	 * <p>{@code cursor} 는 테넌트별 단조 증가라 그 안에서 다음 값을 계산한다 —
	 * {@code pk_tenant_delivery (tenant_id, cursor)} 를 피하려는 것이고 프로듀서도 같은 모양이다.
	 */
	private void deliverNew(long tenantId, String resultId) {
		jdbc.update("""
				INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type,
				       explanation_result_id)
				SELECT ?, COALESCE(MAX(cursor), 0) + 1, 'NEW', ?
				  FROM tenant_delivery WHERE tenant_id = ?
				""", tenantId, resultId, tenantId);
	}

	/**
	 * 그 테넌트에게 <b>무효화 통지</b>를 발번한다(INVALIDATION).
	 *
	 * <p>⚠️ 스키마상 본체 참조는 <b>NULL</b> 이고 대상·사유가 필수다
	 * ({@code ck_tenant_delivery_payload} — 2형상). 그래서 이 행은 경계 SQL 의 앞 두 조건에
	 * 애초에 안 걸리고, 셋째(전체 건수)에만 잡힌다.
	 */
	private void deliverInvalidation(long tenantId, String targetResultId) {
		jdbc.update("""
				INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type,
				       target_explanation_result_id, reason)
				SELECT ?, COALESCE(MAX(cursor), 0) + 1, 'INVALIDATION', ?, '운영자 무효화'
				  FROM tenant_delivery WHERE tenant_id = ?
				""", tenantId, targetResultId, tenantId);
	}

	/** 게시본을 철회 상태로 — 운영자 무효화가 하는 전이({@code JdbcAnalysisWriteRepository}). */
	private void withdraw(String resultId) {
		setStatus(resultId, "WITHDRAWN");
	}

	/**
	 * 게시본을 초안으로 되돌린다.
	 *
	 * <p>⚠️ 비게시가 <b>WITHDRAWN 뿐이 아니라는 것</b>을 픽스처가 보여야 한다 — 전부 철회로만
	 * 만들면 {@code <> 'PUBLISHED'} 를 {@code = 'WITHDRAWN'} 으로 좁히는 변이가 통과하고, 그러면
	 * 발번된 뒤 초안으로 되돌아간 결과가 <b>위반에서 조용히 빠진다</b>(변이 리뷰가 잡았다).
	 */
	private void draft(String resultId) {
		setStatus(resultId, "DRAFT");
	}

	private void setStatus(String resultId, String status) {
		jdbc.update("UPDATE explanation_result SET publication_status = ?"
				+ " WHERE explanation_result_id = ?", status, resultId);
	}

	private void insertTrigger(String id, String tradingDate, String etf) {
		/* `etf_instrument_id` 는 `etf_profile` FK 이고, 그건 다시 `instrument` 를, `instrument` 는
		 * `(instrument_id, entity_type)` 으로 `entity` 를 문다 — 종목 하나에 세 행이 필요하다.
		 * 같은 종목을 여러 날 넣으므로 전부 멱등하게 둔다. */
		jdbc.update("""
				INSERT INTO entity (entity_id, entity_type, display_name)
				VALUES (?, 'INSTRUMENT', ?)
				ON CONFLICT (entity_id) DO NOTHING
				""", etf, etf);
		jdbc.update("""
				INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type)
				VALUES (?, 'XKRX', ?, 'ETF')
				ON CONFLICT (instrument_id) DO NOTHING
				""", etf, etf.toUpperCase());
		jdbc.update("""
				INSERT INTO etf_profile (instrument_id, etf_type) VALUES (?, 'SECTOR')
				ON CONFLICT (instrument_id) DO NOTHING
				""", etf);
		jdbc.update("""
				INSERT INTO price_movement_trigger (price_movement_trigger_id, etf_instrument_id,
				       trade_date, detected_at, observed_return, absolute_gate_triggered,
				       relative_gate_triggered, detection_policy_version)
				VALUES (?,?,?::date,?::timestamptz,-0.05,true,false,'v1')
				""", id, etf, tradingDate,
				// (etf, trade_date, detected_at) 이 UNIQUE 라 같은 종목·날짜의 둘째 행은 시각이
				// 달라야 한다 — DISTINCT 계약을 재려면 그 행이 실제로 들어가야 하기 때문이다.
				"%sT15:%02d:00+09:00".formatted(tradingDate, triggerSeq++ % 60));
	}

	/**
	 * 이미 넣은 작업에 <b>계약·신선도 여섯 컬럼</b>을 채운다({@code ops_expected_task} 의 컬럼이다 —
	 * 별도 테이블이 아니다). 계약이 걸리면 {@code version}·{@code snapshot} 도 NOT NULL 이어야 하고
	 * ({@code ck_ops_expected_task_contract_snapshot}), {@code STALE}·{@code FRESH} 는
	 * {@code observed_at} 을 요구한다({@code ck_ops_expected_task_freshness_pair}).
	 *
	 * <p>⚠️ 인자를 <b>전부 따로 받는다</b>. 하나라도 상수로 박으면 SQL 이 엉뚱한 컬럼을 읽어도
	 * (기대일을 {@code actual_as_of_date} 에서, 수집 시각을 {@code observed_at} 에서) 값이 같아
	 * 통과한다 — 픽스처가 컬럼을 못 가르면 그 컬럼은 계약이 아니다.
	 */
	private void applyContract(String taskId, String contractKey, String version,
			String expectedAsOf, String actualAsOf, String collectedAt, String observedAt,
			String freshnessStatus, String freshnessReason) {
		jdbc.update("""
				UPDATE ops_expected_task
				   SET dataset_contract_key = ?, dataset_contract_version = ?,
				       dataset_contract_snapshot = '{"grain":"daily"}'::jsonb,
				       expected_as_of_date = ?::date, actual_as_of_date = ?::date,
				       collected_at = ?::timestamptz, observed_at = ?::timestamptz,
				       freshness_status = ?, freshness_reason = ?
				 WHERE expected_task_id = ?
				""", contractKey, version, expectedAsOf, actualAsOf, collectedAt, observedAt,
				freshnessStatus, freshnessReason, taskId);
	}

	/** ⚠️ {@code ecs_task_arn} 은 NOT NULL 이고 {@code (expected_task_id, ecs_task_arn)} 이 UNIQUE 다
	 *  — 같은 작업의 시도 둘을 넣으려면 ARN 이 달라야 한다(스키마가 가짜 행을 막는 자리다). */
	private void insertAttempt(String id, String taskId) {
		jdbc.update("""
				INSERT INTO ops_task_attempt (attempt_id, expected_task_id, ecs_task_arn,
				       execution_status)
				VALUES (?,?,?,'SUCCEEDED')
				""", id, taskId, "arn:aws:ecs:task/" + id);
	}

	private void insertMissingSlotIssue(String id, String runKey, String status) {
		insertIssue(id, "PLANNER_MISSING", "slot", runKey, status);
	}

	private void insertIssue(String id, String type, String scope, String scopeKey, String status) {
		jdbc.update("""
				INSERT INTO ops_reconciliation_issue (issue_id, issue_type, scope, scope_key,
				       dedupe_key, status)
				VALUES (?,?,?,?,?,?)
				""", id, type, scope, scopeKey, type.toLowerCase() + ":" + scopeKey, status);
	}

	@Test
	void 날짜를_생략하면_원장이_아는_가장_최근_날을_본다() {
		insertTradingDay("2026-08-01");
		insertTradingDay("2026-08-03");

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/**
	 * 🔴 <b>요청한 날이 그대로 창이 된다.</b> 이걸 안 재면 리포지토리가 인자를 버리고 늘 최신일을
	 * 봐도 아무도 모른다 — 컨트롤러 테스트는 페이크가 받은 값만 보고, 그 값으로 무엇을 하는지는
	 * 페이크가 정하기 때문이다. 화면은 요청 날짜가 아니라 {@code meta.today} 를 그리므로
	 * <b>이상 징후가 화면에 안 나타난다</b>: 과거 조회가 통째로 불능인데 조용하다.
	 */
	@Test
	void 날짜를_주면_최신일이_아니라_그_날을_본다() {
		insertTradingDay("2026-08-01");
		insertTradingDay("2026-08-03");

		assertThat(repository.facts(LocalDate.parse("2026-08-01")).today())
				.isEqualTo(LocalDate.parse("2026-08-01"));
	}

	/**
	 * 런이 하루도 안 뜬 날 — 계획만 있고 런 행이 없는 슬롯도 조회 창 후보다. 런 축만 보면 기본
	 * 조회가 그 날을 건너뛰는데, <b>그날이 바로 콘솔이 열려야 하는 날</b>이다.
	 */
	@Test
	void 계획만_있던_날도_기본_조회의_날짜가_된다() {
		insertTradingDay("2026-08-01");
		insertMissingSlotIssue("i1", "etf-daily:2026-08-03T15:40", "OPEN");

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/**
	 * 🔴 <b>"둘 중 뒤쪽"은 양방향이다.</b> 위 테스트가 "슬롯이 더 뒤" 한 방향만 재서, 비교를 아예
	 * 빼고 슬롯이 있으면 무조건 이기게 만들어도 통과했다. 그러면 3주 전에 열린 채 안 닫힌
	 * {@code PLANNER_MISSING} 하나가 <b>런이 오늘까지 정상인데도 기본 조회를 3주 전으로 되돌린다</b>.
	 */
	@Test
	void 슬롯_날짜가_런보다_과거면_런_날짜가_이긴다() {
		insertTradingDay("2026-08-03");
		insertMissingSlotIssue("i1", "etf-daily:2026-07-20T15:40", "OPEN");

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/**
	 * 🔴 해소된 이슈는 후보가 아니다. {@code status='OPEN'} 술어를 빼도 전건 통과했다 — 그러면
	 * 지난 사고에서 {@code RESOLVED} 된 결손 하나가 <b>기본 조회일을 영구히 그날에 고정</b>한다.
	 */
	@Test
	void 해소된_계획_결손은_조회_창_후보가_아니다() {
		insertTradingDay("2026-08-03");
		insertMissingSlotIssue("i1", "etf-daily:2099-01-01T15:40", "OPEN");
		insertMissingSlotIssue("i2", "etf-daily:2026-08-05T15:40", "RESOLVED");

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/**
	 * 슬롯 스코프의 {@code PLANNER_MISSING} 만 후보다. 두 술어({@code issue_type}·{@code scope})를
	 * 각각 빼도 전건 통과했다.
	 *
	 * <p>⚠️ <b>오늘의 프로듀서로는 이 구멍이 안 터진다</b> — 슬롯 스코프를 쓰는 writer 는
	 * {@code ops/reconciler.py} 의 {@code detect_planner_missing} 하나이고 항상
	 * {@code PLANNER_MISSING} 이며, 비-slot 스코프의 {@code scope_key} 는 <b>런·작업 id</b>라
	 * ({@code run} 은 해시 {@code run_<hex>}, {@code task} 는 ULID {@code etask_01K…}) 날짜
	 * 정규식에 애초에 안 걸린다. 즉 두 술어는 <b>앞으로 생길 프로듀서</b>에 대한 가드이고,
	 * 이 테스트가 지키는 것은 <b>그 가드가 조용히 빠지는 것</b>이다.
	 *
	 * <p>(이 주석은 한때 "런 키와 슬롯 키 형식이 같아 지금도 터진다"고 적혀 있었고 <b>틀렸다</b> —
	 * `scope_key` 에 실제로 무엇이 들어가는지 안 보고 쓴 문장이었다.)
	 */
	@Test
	void 슬롯_스코프가_아닌_이슈는_조회_창_후보가_아니다() {
		insertTradingDay("2026-08-03");
		/* 픽스처는 **날짜 형식을 담은** scope_key 를 일부러 쓴다 — 미래 프로듀서가 그렇게 쓰기
		 * 시작해도 두 술어가 막는지를 재는 것이 이 테스트의 일이라서다. 둘의 날짜를 다르게 둬야
		 * 어느 술어가 빠졌는지 실패 값으로 갈린다. */
		insertIssue("i1", "PLANNER_MISSING", "run", "etf-daily:2026-08-05T15:40", "OPEN");
		insertIssue("i2", "MISSED", "slot", "etf-daily:2026-08-06T15:40", "OPEN");

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/**
	 * 거래일이 NULL 인 런(비거래일 레인)은 계획 시각의 KST 날짜로 줍는다. 거래일만 보면 통째로
	 * 새어 나가 그 날의 사실이 기본 화면에서 사라진다.
	 *
	 * <p>08-02 16:00Z = 08-03 01:00 KST — UTC 로 세면 하루가 밀린다.
	 */
	@Test
	void 거래일이_없는_런은_계획_시각의_KST_날짜로_줍는다() {
		insertRun("r-news", "news:2026-08-03T15:30", "news", "SUCCEEDED", null,
				"2026-08-02T16:00:00Z", "2026-08-02T16:00:00Z", null);

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/** 미래 런 한 건이 기본 조회를 오지 않은 날로 옮기면 그 화면의 사실은 전부 빈다. */
	@Test
	void 미래_거래일_런은_기본_조회의_날짜가_되지_않는다() {
		insertTradingDay("2026-08-03");
		insertTradingDay("2099-01-01");

		assertThat(repository.facts(null).today()).isEqualTo(DAY);
	}

	/**
	 * 미래 슬롯 키가 하나라도 있으면 기본 조회가 오지 않은 날로 뛴다(요청 파라미터의 미래 날짜를
	 * 400 으로 막은 것과 같은 이유 — 이쪽은 파라미터가 아니라 원장에서 온다).
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

	/**
	 * 런 축은 <b>그 날의 것만</b> 나간다. 창을 안 걸면 원장 전건이 실려 다른 날 런이 오늘 사건이
	 * 된다. {@code run_key} 순 고정도 함께 잰다 — 정렬이 없으면 같은 원장이 조회마다 다른 순서로
	 * 나가 소비자의 "첫 런"이 흔들린다({@code run_key} 가 UNIQUE 라 그 하나로 전순서가 정해진다).
	 */
	@Test
	void 런_축은_그_날의_런만_컬럼_그대로_정렬해서_싣는다() {
		/* 🔴 **전 필드를 단언한다.** `runKey` 만 재던 동안 SQL 이 다른 컬럼을 읽게 만드는 변이
		 * 다섯이 전부 살아남았다(레인을 `schedule_slot` 에서 · 갱신 시각을 `created_at` 에서 ·
		 * 거래일·마감을 NULL 로). 그때 화면은 조용히 틀린 사실 위에 판정을 세운다.
		 * 그래서 픽스처의 값을 **컬럼마다 다르게** 둔다 — 같으면 못 가른다. */
		/* ⚠️ **삽입 순서를 기대 순서와 반대로 둔다.** 같으면 `ORDER BY` 를 통째로 지워도 통과한다 —
		 * Postgres 가 힙 순서(=삽입 순서)로 주기 때문이다. 그러면 이 테스트가 잡는 것은 *틀린
		 * 정렬*뿐이고 계약이 지키려는 *정렬 부재*는 새어 나간다(실제로 한 라운드 그랬다). */
		insertRun("r-a", "etf-daily:2026-08-03T15:40", "etf-daily", "SUCCEEDED", "2026-08-03",
				"2026-08-03T06:40:00Z", "2026-08-03T07:20:34Z", "2026-08-03T08:00:00Z");
		/* 🔴 **같은 레인의 두 슬롯**을 일부러 둔다(뉴스가 하루 00:10·08:10 두 번 도는 것처럼).
		 * 레인은 슬롯 키의 접두사라, 레인별로 하나씩만 있으면 `lane` 으로 정렬해도 `run_key` 로
		 * 정렬해도 결과가 같아 **정렬 키를 바꾸는 변이가 살아남는다**. 레인이 같은 둘이 있어야
		 * 그 키가 순서를 못 정한다는 게 드러난다 — 계약이 막으려는 "첫 런이 흔들린다"가 그것이다. */
		insertRun("r-c", "etf-daily:2026-08-03T09:00", "etf-daily", "SUCCEEDED", "2026-08-03",
				"2026-08-03T00:00:00Z", "2026-08-03T00:30:00Z", null);
		insertRun("r-b", "news:2026-08-03T09:00", "news", "RUNNING", null,
				"2026-08-03T00:00:00Z", "2026-08-03T00:10:00Z", null);
		insertTradingDay("2026-08-01");

		assertThat(repository.facts(DAY).runs()).containsExactly(
				new RunRow("etf-daily:2026-08-03T09:00", "etf-daily", DAY, "SUCCEEDED",
						OffsetDateTime.parse("2026-08-03T00:30:00Z"), null, null, null),
				new RunRow("etf-daily:2026-08-03T15:40", "etf-daily", DAY, "SUCCEEDED",
						OffsetDateTime.parse("2026-08-03T07:20:34Z"),
						OffsetDateTime.parse("2026-08-03T08:00:00Z"), null, null),
				new RunRow("news:2026-08-03T09:00", "news", null, "RUNNING",
						OffsetDateTime.parse("2026-08-03T00:10:00Z"), null, null, null));
	}

	/**
	 * 거래일이 NULL 인 런도 <b>같은 창</b>에 들어와야 한다 — 조회 창을 고르는 식과 런을 자르는 식이
	 * 갈리면 그 런이 "날짜는 골랐는데 목록에는 없는" 상태가 된다.
	 */
	@Test
	void 거래일이_없는_런도_같은_창에_잡힌다() {
		insertRun("r-news", "news:2026-08-03T15:30", "news", "SUCCEEDED", null,
				"2026-08-02T16:00:00Z", "2026-08-02T16:00:00Z", null);

		assertThat(repository.facts(DAY).runs()).extracting(RunRow::runKey)
				.containsExactly("news:2026-08-03T15:30");
	}

	/** 런 행이 없는 계획 슬롯은 <b>런처럼 생긴 행</b>으로 나간다 — 그 슬롯이 사건의 대상이다. */
	@Test
	void 런_행이_없는_계획_슬롯은_런처럼_생긴_행으로_나간다() {
		insertMissingSlotIssue("i1", "etf-daily:2026-08-03T15:40", "OPEN");

		assertThat(repository.facts(DAY).runs()).singleElement().satisfies(r -> {
			assertThat(r.runKey()).isEqualTo("etf-daily:2026-08-03T15:40");
			assertThat(r.lane()).isEqualTo("etf-daily");
			assertThat(r.tradingDate()).isEqualTo(DAY);
			assertThat(r.planned()).isTrue();
			assertThat(r.noRunRow()).isTrue();
			/* 런 행이 없으니 원장 상태·시각은 **없는 것이 사실**이다 — 채우면 지어내는 것이다. */
			assertThat(r.ledgerStatus()).isNull();
			assertThat(r.ledgerUpdated()).isNull();
			assertThat(r.deadline()).isNull();
		});
	}

	/**
	 * 🔴 이슈가 아직 OPEN 인 채 런이 생기면 같은 {@code run_key} 가 <b>두 행</b>으로 나간다 —
	 * 소비자는 그걸 식별자 충돌로 읽어 그 축 규칙을 통째로 못 돎 으로 세운다. {@code status} 만
	 * 믿으면 그 창이 열린다(Reconciler 가 닫기 전까지가 그 창이다).
	 */
	@Test
	void 런이_생긴_뒤_안_닫힌_이슈는_유령_행을_만들지_않는다() {
		insertTradingDay("2026-08-03");
		insertMissingSlotIssue("i1", "etf-daily:2026-08-03T15:40", "OPEN");

		assertThat(repository.facts(DAY).runs()).extracting(RunRow::runKey)
				.containsExactly("etf-daily:2026-08-03T15:40");
	}

	/**
	 * 슬롯 키를 못 읽으면 레인·거래일이 null 이다 — 잘못 자른 조각을 레인 이름이라고 우기면
	 * 화면이 존재하지 않는 레인을 그린다. 사건 축({@code run_key})은 그대로 남는다.
	 */
	@Test
	void 형식이_깨진_슬롯_키는_레인을_지어내지_않고_경고를_남긴다() {
		insertMissingSlotIssue("i1", ":2026-08-03T15:40", "OPEN");
		/* 로거는 전역이라 반드시 떼야 다음 테스트로 안 샌다 — 레포 선례(publication-api 의
		 * `ExplanationDisclaimerIntegrationTest`)와 같은 try/finally 형태다. */
		Logger repoLogger = (Logger) LoggerFactory.getLogger(JdbcConsoleFactsRepository.class);
		ListAppender<ILoggingEvent> captured = new ListAppender<>();
		captured.start();
		repoLogger.addAppender(captured);
		try {
			assertThat(repository.facts(DAY).runs()).singleElement().satisfies(r -> {
				assertThat(r.runKey()).isEqualTo(":2026-08-03T15:40");
				assertThat(r.lane()).isNull();
				assertThat(r.tradingDate()).isNull();
				assertThat(r.noRunRow()).isTrue();
			});
			/* null 을 내는 것만으로는 Rule 12 를 못 만족한다 — 응답에는 "레인 미상"으로만 보여
			 * Planner 키 형식이 갈렸다는 사실이 아무 데도 안 남는다. 경고가 그 유일한 장치라면
			 * **경고의 존재가 곧 계약이다**. 레벨까지 재는 것은 선례와 같은 이유다 — 강등하면
			 * 로거가 걸러 안 들어오지만, ERROR 로 올리는 변이는 레벨을 안 보면 통과한다. */
			assertThat(captured.list).anySatisfy(event -> {
				assertThat(event.getLevel()).isEqualTo(Level.WARN);
				assertThat(event.getFormattedMessage()).contains("슬롯 키를 못 읽었다");
			});
		}
		finally {
			repoLogger.detachAppender(captured);
		}
	}

	/**
	 * 이 쿼리의 술어 <b>다섯</b>을 한 자리에서 잰다 — 하나라도 빠지면 <b>유령 런 행</b>이
	 * `runs[]` 에 뜬다(`planned: true, noRunRow: true` 를 달고). 화면은 그걸 "계획됐는데 안 돈
	 * 슬롯"으로 그리고, 존재한 적 없는 사건이 판정 대상이 된다.
	 *
	 * <p>⚠️ 조회 창 쪽 형제 쿼리({@code MISSING_SLOT_DAYS_SQL})는 같은 술어를 이미 pin 했는데
	 * 이쪽만 비어 있었다 — 픽스처 키가 <b>조회 날짜와 달라</b> 그 테스트가 이 쿼리를 안 탔다.
	 * 그래서 여기 후보는 전부 <b>08-03 키</b>다.
	 */
	@Test
	void 슬롯_후보가_아닌_이슈는_유령_런_행을_만들지_않는다() {
		insertMissingSlotIssue("i1", "etf-daily:2026-08-03T15:40", "RESOLVED");   // status
		insertMissingSlotIssue("i2", "etf-daily:2026-08-02T15:40", "OPEN");       // 다른 날
		insertIssue("i3", "MISSED", "slot", "etf-daily:2026-08-03T09:00", "OPEN");        // issue_type
		insertIssue("i4", "PLANNER_MISSING", "run", "etf-daily:2026-08-03T10:00", "OPEN"); // scope
		/* `LIKE '%:' || ? || 'T%'` 의 앵커(`:` 와 `T`)를 잰다 — 날짜가 키의 **다른 자리**에 박힌
		 * 값이 통과하면 안 된다. 앵커를 지우면(`'%' || ? || '%'`) 이 줄이 잡는다. */
		insertIssue("i5", "PLANNER_MISSING", "slot", "run-2026-08-03-retry", "OPEN");

		assertThat(repository.facts(DAY).runs()).isEmpty();
	}

	/**
	 * 두 소스가 <b>한 축</b>으로 합쳐진다 — 각 소스가 자기 안에서만 정렬돼 있어 그냥 이어 붙이면
	 * 전체 순서가 깨진다. 계획 슬롯이 실재 런들 <b>사이</b>에 끼는 픽스처라야 그걸 잰다.
	 */
	@Test
	void 계획_슬롯과_실재_런이_한_축으로_정렬된다() {
		insertRun("r-z", "z-lane:2026-08-03T15:40", "z-lane", "SUCCEEDED", "2026-08-03",
				"2026-08-03T06:40:00Z", "2026-08-03T06:40:00Z", null);
		insertRun("r-a", "a-lane:2026-08-03T15:40", "a-lane", "SUCCEEDED", "2026-08-03",
				"2026-08-03T06:40:00Z", "2026-08-03T06:40:00Z", null);
		insertMissingSlotIssue("i1", "m-lane:2026-08-03T15:40", "OPEN");

		assertThat(repository.facts(DAY).runs()).extracting(RunRow::runKey)
				.containsExactly("a-lane:2026-08-03T15:40", "m-lane:2026-08-03T15:40",
						"z-lane:2026-08-03T15:40");
	}

	/**
	 * 작업 축은 <b>완전성 jsonb 와 시도 수</b>를 함께 낸다. 그리고 {@code getLong} 이 SQL NULL 을
	 * 0 으로 주므로 <b>"0건 처리"와 "신호 없음"이 갈려야 한다</b> — null 을 0 으로 접으면 화면이
	 * 계측 공백을 실측으로 그린다.
	 */
	@Test
	void 작업_축은_완전성_jsonb_와_시도_수를_함께_낸다() {
		insertTradingDay("2026-08-03");
		insertTask("t1", "r-2026-08-03", "COLLECT", "raw", "price", "FULFILLED", 906L, 0L,
				"{\"expected\":33,\"received\":30,\"missing\":3}");
		insertTask("t2", "r-2026-08-03", "LOAD", "feature", "price", "PENDING", false, null, null,
				null);
		insertAttempt("a1", "t1");
		insertAttempt("a2", "t1");

		assertThat(repository.facts(DAY).tasks()).satisfiesExactly(
				t -> {
					assertThat(t.taskKey()).isEqualTo("COLLECT");
					/* 런과 같은 축으로 매인다 — 내부 id 면 와이어에서 런 축과 안 이어진다. */
					assertThat(t.runKey()).isEqualTo("etf-daily:2026-08-03T15:40");
					assertThat(t.pipelineType()).isEqualTo("etf-daily");
					assertThat(t.tradingDate()).isEqualTo(DAY);
					assertThat(t.stage()).isEqualTo("raw");
					assertThat(t.dataset()).isEqualTo("price");
					assertThat(t.required()).isTrue();
					assertThat(t.planStatus()).isEqualTo("DUE");
					assertThat(t.taskOutcome()).isEqualTo("FULFILLED");
					assertThat(t.required()).isTrue();
					assertThat(t.dataStatus()).isEqualTo("VALID");
					assertThat(t.recordsOut()).isEqualTo(906L);
					assertThat(t.failedRecords()).isZero();
					/* 세 값을 **서로 다르게** 둔다 — 같으면 `expected`↔`received` 키를 맞바꾸는
					 * 변이가 통과한다(조각 2 의 `created_at`==`updated_at` 과 같은 병이다). */
					assertThat(t.completenessExpected()).isEqualTo(33L);
					assertThat(t.completenessReceived()).isEqualTo(30L);
					assertThat(t.completenessMissing()).isEqualTo(3L);
					assertThat(t.attempts()).isEqualTo(2L);
				},
				t -> {
					/* 🔴 여기가 요점이다 — 원장이 안 준 값은 **null 이지 0 이 아니다**. */
					assertThat(t.taskKey()).isEqualTo("LOAD");
					assertThat(t.required()).isFalse();   // 상수 true 로 바꾸는 변이를 잡는다
					assertThat(t.dataStatus()).isEqualTo("UNKNOWN");
					assertThat(t.recordsOut()).isNull();
					assertThat(t.failedRecords()).isNull();
					assertThat(t.completenessExpected()).isNull();
					assertThat(t.attempts()).isZero();   // count(*) 라 0 이 실측이다
				});
	}

	/**
	 * stage 정렬을 CASE 로 고정한다 — 문자열 순이면 파이프라인이 <b>역순</b>이 된다
	 * ({@code feature} < {@code normalize} < {@code raw}). 픽스처를 그 함정에 정확히 걸리게 둔다:
	 * 삽입 순서·문자열 순 둘 다 기대와 달라야 정렬이 실제로 일한 것이 보인다.
	 */
	@Test
	void 작업_축은_런_그다음_파이프라인_순서로_정렬된다() {
		insertTradingDay("2026-08-03");
		insertTask("t1", "r-2026-08-03", "LOAD", "feature", "price", "PENDING", null, null, null);
		insertTask("t2", "r-2026-08-03", "CLEAN", "normalize", "price", "PENDING", null, null, null);
		insertTask("t3", "r-2026-08-03", "COLLECT", "raw", "price", "PENDING", null, null, null);
		/* 🔴 **두 번째 런의 작업**을 둔다 — 전부 한 런 밑에 있으면 1차 정렬 키(`run_key`)를
		 * 지워도 통과한다. 이 런은 `run_key` 가 앞서므로(`etf-daily:…09:00`) 그 작업이 먼저 와야
		 * 하고, 그 안에서 stage 순이 다시 선다. */
		insertRun("r-early", "etf-daily:2026-08-03T09:00", "etf-daily", "SUCCEEDED", "2026-08-03",
				"2026-08-03T00:00:00Z", "2026-08-03T00:00:00Z", null);
		insertTask("t4", "r-early", "COLLECT", "raw", "price", "PENDING", null, null, null);

		assertThat(repository.facts(DAY).tasks()).extracting(TaskRow::runKey, TaskRow::stage)
				.containsExactly(
						org.assertj.core.groups.Tuple.tuple("etf-daily:2026-08-03T09:00", "raw"),
						org.assertj.core.groups.Tuple.tuple("etf-daily:2026-08-03T15:40", "raw"),
						org.assertj.core.groups.Tuple.tuple("etf-daily:2026-08-03T15:40", "normalize"),
						org.assertj.core.groups.Tuple.tuple("etf-daily:2026-08-03T15:40", "feature"));
	}

	/**
	 * 🔴 <b>계약·신선도 여섯 컬럼</b>이 각자 제 컬럼에서 온다. 이 여섯은 와이어의 작업 축에 안
	 * 나가고 서비스가 데이터셋 축으로 접어서 내보내므로, <b>여기서 안 재면 어디서도 안 재진다</b> —
	 * {@code expected_as_of_date} 와 {@code actual_as_of_date} 를 SQL 에서 맞바꿔도 전건이 초록이던
	 * 자리다.
	 *
	 * <p>그래서 픽스처가 여섯을 <b>전부 서로 다르게</b> 둔다. 특히:
	 * <ul>
	 *   <li>as-of 둘이 달라야 맞바꿈이 걸린다 — 스키마상 {@code FRESH} 는 {@code actual = expected}
	 *       를 요구하므로({@code ck_ops_expected_task_verified_as_of}) 갈리는 픽스처의 상태는
	 *       <b>{@code STALE}</b> 이다. FRESH 로 두면 두 값이 같아 이 축이 통째로 안 재진다.</li>
	 *   <li>🔴 {@code expected_as_of_date} 를 런의 <b>{@code trading_date} 와도 다르게</b> 둔다.
	 *       둘을 같은 날로 두면 {@code t.expected_as_of_date} 대신 {@code r.trading_date} 를 읽는
	 *       변이가 통과한다 — 이 조회는 두 테이블을 조인하므로 옆 <b>테이블</b>의 DATE 컬럼도
	 *       후보다(리뷰가 잡았다).</li>
	 *   <li>{@code collected_at} 옆에 <b>{@code observed_at} 을 다른 시각으로</b> 둔다. 둘 다
	 *       TIMESTAMPTZ 라 SQL 이 옆 컬럼을 읽어도 형이 맞아 조용히 통과한다.</li>
	 *   <li>계약 <b>key 와 version</b> 을 다르게 둔다 — 둘 다 TEXT 다.</li>
	 * </ul>
	 *
	 * <p>계약이 안 걸린 작업을 함께 둬서 여섯이 <b>상수가 아님</b>을 잰다. 그 행의 여섯은 전부
	 * NULL 인데, 그건 "계약 미적용"이지 "UNKNOWN" 이 아니다(마이그레이션 주석의 구분).
	 */
	@Test
	void 계약_신선도_여섯_컬럼은_각자_제_컬럼에서_온다() {
		insertTradingDay("2026-08-03");
		insertTask("t1", "r-2026-08-03", "COLLECT", "raw", "etf_holdings", "FULFILLED", 906L, 0L,
				null);
		/* 거래일(08-03)·기대일(08-02)·실제일(08-01)이 전부 다르다 — 셋 다 DATE 라 어느 둘이 같으면
		 * 그 짝을 맞바꾸는 변이가 통과한다. STALE 이라야 actual < expected 가 스키마에 선다. */
		applyContract("t1", "ETF_HOLDINGS_KRX_EOD", "v3", "2026-08-02", "2026-08-01",
				"2026-08-03T07:00:00Z", "2026-08-03T08:00:00Z", "STALE",
				"ACTUAL_AS_OF_BEFORE_EXPECTED");
		insertTask("t2", "r-2026-08-03", "LOAD", "feature", "price", "PENDING", null, null, null);

		assertThat(repository.facts(DAY).tasks()).satisfiesExactly(
				t -> {
					assertThat(t.taskKey()).isEqualTo("COLLECT");
					assertThat(t.datasetContractKey()).isEqualTo("ETF_HOLDINGS_KRX_EOD");
					// 거래일(08-03)이 아니라 기대일(08-02)이다 — 옆 테이블의 DATE 를 읽으면 걸린다.
					assertThat(t.tradingDate()).isEqualTo(DAY);
					assertThat(t.expectedAsOf()).isEqualTo(LocalDate.parse("2026-08-02"));
					assertThat(t.actualAsOf()).isEqualTo(LocalDate.parse("2026-08-01"));
					assertThat(t.collectedAt())
							.isEqualTo(OffsetDateTime.parse("2026-08-03T07:00:00Z"));
					assertThat(t.freshnessStatus()).isEqualTo("STALE");
					assertThat(t.freshnessReason()).isEqualTo("ACTUAL_AS_OF_BEFORE_EXPECTED");
				},
				t -> {
					/* 🔴 계약이 없는 작업 — 여섯이 전부 null 이라야 위 값들이 상수가 아님이 선다. */
					assertThat(t.taskKey()).isEqualTo("LOAD");
					assertThat(t.datasetContractKey()).isNull();
					assertThat(t.expectedAsOf()).isNull();
					assertThat(t.actualAsOf()).isNull();
					assertThat(t.collectedAt()).isNull();
					assertThat(t.freshnessStatus()).isNull();
					assertThat(t.freshnessReason()).isNull();
				});
	}

	/** 창이 없으면 다른 날 작업이 오늘 사건이 된다 — 런 축과 <b>같은 식</b>을 써야 한다. */
	@Test
	void 작업_축도_그_날의_것만_나간다() {
		insertTradingDay("2026-08-03");
		insertTradingDay("2026-08-01");
		insertTask("t1", "r-2026-08-03", "COLLECT", "raw", "price", "FULFILLED", 1L, 0L, null);
		insertTask("t2", "r-2026-08-01", "COLLECT", "raw", "price", "FULFILLED", 1L, 0L, null);

		assertThat(repository.facts(DAY).tasks()).extracting(TaskRow::runKey)
				.containsExactly("etf-daily:2026-08-03T15:40");
	}

	/**
	 * 산출 축은 <b>그 날의 값과 직전 거래일 중앙값</b>을 함께 낸다. 표본이 짝수면 가운데 둘의
	 * 평균이고, 결과에 없는 거래일은 <b>0 이 실측</b>이다(모름이 아니다).
	 *
	 * <p>⚠️ 값을 날마다 <b>다르게</b> 둔다 — 같은 값이면 중앙값을 평균·최댓값·첫값 무엇으로 바꿔도
	 * 통과한다. 그리고 {@code today} 는 표본에서 <b>빠져야</b> 하므로 그날 값을 표본과 다르게 둔다.
	 */
	@Test
	void 산출은_그날_값과_직전_거래일_중앙값을_함께_낸다() {
		insertTradingDay("2026-08-03");   // 월
		insertTradingDay("2026-07-31");   // 금
		insertTradingDay("2026-07-30");   // 목
		insertTradingDay("2026-07-29");   // 수
		/* ⚠️ 표본 날짜는 **평일**이어야 한다 — 주말은 표본에서 빠지므로(달력 규칙) 토·일을 쓰면
		 * 그 날이 통째로 안 세진다.
		 *
		 * 표본 셋을 **치우치게** 둔다: 07-29 → 0종, 07-31 → 1종, 07-30 → 5종.
		 * ⇒ 중앙값 1.0 · 평균 2.0 — 대칭으로 두면 둘이 같아져 **중앙값을 평균으로 바꾸는 변이가
		 * 통과한다**(실제로 통과했다). 통계량 자체가 계약이면 표본이 그걸 갈라야 한다. */
		insertTrigger("g1", "2026-07-31", "etf-a");
		insertTrigger("g3", "2026-07-30", "etf-a");
		insertTrigger("g4", "2026-07-30", "etf-b");
		insertTrigger("g5", "2026-07-30", "etf-c");
		insertTrigger("g6", "2026-07-30", "etf-d");
		insertTrigger("g9", "2026-07-30", "etf-e");
		/* 그날 값: 1종. 같은 ETF 를 **두 번** 넣어도 `DISTINCT` 라 1 이어야 한다 —
		 * `count(*)` 로 바꾸는 변이가 여기서 죽는다. */
		insertTrigger("g7", DAY.toString(), "etf-a");
		insertTrigger("g8", DAY.toString(), "etf-a");

		OutputRow trig = repository.facts(DAY).outputs().stream()
				.filter(o -> o.id().equals("o.trig")).findFirst().orElseThrow();
		assertThat(trig.label()).isEqualTo("배치 트리거");
		assertThat(trig.unit()).isEqualTo("종");
		assertThat(trig.today()).isEqualTo(1L);
		// 정렬 [0, 1, 5] 의 가운데 = 1.0 (평균이면 2.0 이다)
		assertThat(trig.base()).isEqualTo(1.0d);
	}

	/**
	 * 🔴 <b>휴장일은 표본에서 뺀다.</b> {@code trading_date} 는 거래일 달력이 아니라 슬롯 날짜라
	 * ({@code ops/planner.py} 의 {@code plan_run} 이 {@code slot.date()} 를 그대로 넘긴다) 휴장일에도 런 행이 생긴다.
	 * 안 빼면 그날의 산출 0 이 표본에 들어가 중앙값이 내려가고 편차 판정이 둔해진다.
	 *
	 * <p>⚠️ <b>제외는 날짜 단위지 런 단위가 아니다.</b> 휴장 신호는 KR 시장 레인에만 붙는데 뉴스
	 * 레인은 휴장일에도 돈다 — 그래서 픽스처가 <b>같은 휴장일에 뉴스 런을 함께</b> 둔다. 런 단위로
	 * 상관시키면 그 뉴스 런이 같은 날짜를 표본에 되살려 이 단언이 깨진다.
	 */
	@Test
	void 휴장일은_기준_표본에서_빠진다() {
		insertTradingDay("2026-08-03");
		insertTradingDay("2026-07-31");
		insertHoliday("2026-07-30");   // 평일(목)인데 휴장 — 달력이 아니라 원장이 답하는 자리
		// 같은 휴장일에 도는 다른 레인(뉴스) — 런 단위 제외였다면 이 런이 07-30 을 되살린다.
		insertRun("r-news-0730", "news:2026-07-30T15:30", "news", "SUCCEEDED", "2026-07-30",
				"2026-07-30T06:30:00Z", "2026-07-30T06:30:00Z", null);
		/* 🔴 **같은 휴장일의 계획 결손 슬롯도 둔다.** 제외를 런 날짜 소스 안에서만 하고 `slotDays`
		 * 를 안 거르면 이 이슈가 07-30 을 표본에 **되살린다** — 그게 "합집합 뒤 한 번"이 지키는
		 * 것이고, 이 행이 없으면 그 변이가 통과한다(리뷰가 잡았다). */
		insertMissingSlotIssue("i-hol", "news:2026-07-30T00:10", "OPEN");
		/* 🔴 **정상 거래일에도 기대 작업을 둔다.** 안 두면 `HOLIDAY_DAYS_SQL` 의
		 * `JOIN ops_expected_task` 가 그 날 0행이라, `skip_reason='NON_TRADING_DAY'` 술어를
		 * **통째로 지워도** 결과가 같아진다(변이 리뷰가 잡았다 — 그 줄이 없으면 운영에선 모든 날이
		 * 휴장으로 판정돼 기준이 전부 사라진다). 원장에서도 모든 런은 기대 작업을 갖는다. */
		insertTask("t-0731", "r-2026-07-31", "COLLECT", "raw", "price", "FULFILLED", 1L, 0L, null);
		// 07-31 만 표본이면 중앙값 = 2.0. 07-30(산출 0)이 섞이면 1.0 으로 내려간다.
		insertTrigger("g1", "2026-07-31", "etf-a");
		insertTrigger("g2", "2026-07-31", "etf-b");

		OutputRow trig = repository.facts(DAY).outputs().stream()
				.filter(o -> o.id().equals("o.trig")).findFirst().orElseThrow();
		assertThat(trig.base()).isEqualTo(2.0d);
	}

	/**
	 * 🔴 <b>오늘이 휴장이면 장 산출의 기준을 안 준다.</b> 그날 0 은 실측이 맞지만 <b>비교할 평소가
	 * 없는 날</b>이라, 기준을 주면 소비자가 −100% 편차로 판정한다. 없는 사실을 지어내지 않고 이미
	 * 있는 "기준 없음" 규약을 탄다.
	 *
	 * <p>⚠️ 뉴스 갈래는 휴장일에도 도니까 <b>기준을 그대로 준다</b> — 이 구분이 {@code marketBound}
	 * 이고, 한쪽만 재면 그 플래그를 상수로 바꾸는 변이가 통과한다.
	 */
	@Test
	void 오늘이_휴장이면_장_산출만_기준을_안_준다() {
		insertHoliday(DAY.toString());
		insertTradingDay("2026-07-31");
		insertTrigger("g1", "2026-07-31", "etf-a");

		List<OutputRow> outputs = repository.facts(DAY).outputs();
		assertThat(outputs).extracting(OutputRow::id)
				.containsExactly("o.pub", "o.trig", "o.doc", "o.asr", "o.evt");
		assertThat(outputs).filteredOn(o -> o.id().equals("o.trig")).singleElement()
				.satisfies(o -> {
					assertThat(o.today()).isZero();   // 실측 0 은 그대로 낸다
					assertThat(o.base()).isNull();    // 비교할 평소가 없다
				});
		// 장에 매인 산출은 **둘 다** 그렇다 — 한쪽만 재면 그 플래그를 개별로 뒤집는 변이가 산다.
		assertThat(outputs).filteredOn(o -> o.id().equals("o.pub")).singleElement()
				.satisfies(o -> assertThat(o.base()).isNull());
		// 장에 안 매인 산출은 휴장일에도 기준을 준다(표본 하나 → 중앙값 0.0, null 이 아니다).
		assertThat(outputs).filteredOn(o -> o.id().equals("o.doc")).singleElement()
				.satisfies(o -> assertThat(o.base()).isEqualTo(0.0d));
	}

	/**
	 * 🔴 <b>표본이 없으면 기준은 null 이다 — 0 이 아니다.</b> 원장에 직전 거래일이 하나도 없으면
	 * "평소가 0" 이 아니라 "평소를 모른다"이고, 0 으로 메우면 소비자가 그 산출을 판정 대상으로 세운다.
	 */
	@Test
	void 직전_거래일이_없으면_기준은_null_이다() {
		insertTradingDay(DAY.toString());
		insertTrigger("g1", DAY.toString(), "etf-a");

		assertThat(repository.facts(DAY).outputs())
				.filteredOn(o -> o.id().equals("o.trig")).singleElement()
				.satisfies(o -> {
					assertThat(o.today()).isEqualTo(1L);
					assertThat(o.base()).isNull();
				});
	}

	/**
	 * 🔴 <b>계획 결손일도 기준 표본이다.</b> Planner 가 통째로 실패한 날은 {@code ops_pipeline_run}
	 * 에 한 행도 없어 런 조회로는 안 잡힌다 — 빼면 <b>그날의 실측 0 이 표본에서 사라져</b> 중앙값이
	 * 올라가고, 편차 판정은 양방향이라 위쪽 이상이 조용해진다.
	 */
	@Test
	void 계획_결손일도_기준_표본에_들어간다() {
		insertTradingDay("2026-08-03");
		insertTradingDay("2026-07-31");
		// 07-30(목)은 런이 0건이고 계획 결손 이슈만 있다.
		insertMissingSlotIssue("i1", "etf-daily:2026-07-30T15:40", "OPEN");
		insertTrigger("g1", "2026-07-31", "etf-a");
		insertTrigger("g2", "2026-07-31", "etf-b");

		// 표본 = {07-30: 0, 07-31: 2} ⇒ 중앙값 1.0. 07-30 이 빠지면 2.0 이 된다.
		assertThat(repository.facts(DAY).outputs())
				.filteredOn(o -> o.id().equals("o.trig")).singleElement()
				.satisfies(o -> assertThat(o.base()).isEqualTo(1.0d));
	}

	/**
	 * 🔴 <b>자르기는 휴장일 제외 뒤에 한다.</b> 먼저 10개로 자르고 그 안에 휴장일이 있으면 표본이
	 * 9개로 줄고 <b>11번째 거래일은 영영 안 들어온다</b>. 그래서 픽스처를 정확히 그 함정에 건다 —
	 * 최근 11일 중 하나가 휴장이라, 순서가 뒤바뀌면 표본이 10개가 아니라 9개가 된다.
	 */
	@Test
	void 표본은_휴장일을_뺀_뒤_10개로_자른다() {
		insertTradingDay(DAY.toString());
		insertHoliday("2026-07-27");   // day 직전 **평일 12개** 후보 중 하나가 휴장이다
		/* 후보는 day 직전 평일 12개(07-31·30·29·28·27·24·23·22·21·20·17·16)이고 07-27 이 휴장이라
		 * **비휴장 11개**가 남는다. 창이 10 이므로 옳은 구현은 **가장 오래된 07-16 을 버린다**.
		 *
		 * 후보를 11개만 두면(=창과 같은 수) 자르기가 no-op 이 되어 **창 크기도 정렬 방향도 안 재진다**
		 * — `limit(10)`→11·20·제거, 그리고 '최근 10' 대신 '가장 오래된 10' 이 전부 살아남았다
		 * (변이 리뷰가 잡았다). 그래서 후보를 하나 더 둔다.
		 *
		 * 값(오래된→최신): 07-16=**3**, 07-17·20·21·22=0, 07-23·24·28·29·30=1, 07-31=0
		 *   · 옳게(최근 10, 07-16 제외) → [0,0,0,0,0,1,1,1,1,1] 가운데 둘 평균 = **0.5**
		 *   · 자르기를 제외 앞으로      → 9개                                  = 1.0
		 *   · 창을 11·20·무제한으로     → 07-16 이 들어와 11개                 = 1.0
		 *   · '가장 오래된 10' 으로     → 07-31 이 빠지고 3 이 들어옴           = 1.0
		 * 값을 균일하게 두면 개수가 달라져도 중앙값이 같아 이 계약들이 안 재진다. */
		/* ⚠️ 전부 평일이다 — 주말을 섞으면 그 날이 후보에서 먼저 빠져 수가 안 맞는다. */
		List<String> zeroDays = List.of("2026-07-17", "2026-07-20", "2026-07-21", "2026-07-22",
				"2026-07-31");
		List<String> oneDays = List.of("2026-07-23", "2026-07-24", "2026-07-28", "2026-07-29",
				"2026-07-30");
		insertTradingDay("2026-07-16");
		zeroDays.forEach(this::insertTradingDay);
		oneDays.forEach(this::insertTradingDay);
		// 가장 오래된 날에만 3종 — 창 크기·정렬 방향이 값으로 드러나게 하는 바깥값이다.
		insertTrigger("gx1", "2026-07-16", "etf-x1");
		insertTrigger("gx2", "2026-07-16", "etf-x2");
		insertTrigger("gx3", "2026-07-16", "etf-x3");
		for (int i = 0; i < oneDays.size(); i++) {
			insertTrigger("g" + i, oneDays.get(i), "etf-" + i);
		}

		assertThat(repository.facts(DAY).outputs())
				.filteredOn(o -> o.id().equals("o.trig")).singleElement()
				.satisfies(o -> assertThat(o.base()).isEqualTo(0.5d));
	}

	/**
	 * 🔴 <b>주말은 원장이 답해 주지 않는다.</b> {@code NON_TRADING_DAY} 신호는 달력에 매인 레인이
	 * 그날 <b>실제로 돌았을 때만</b> 생긴다 — 뉴스 레인은 주 7일 돌고 그 런에도 {@code trading_date}
	 * 가 박히므로, 시장 레인이 안 돈 주말은 원장상 평범한 거래일처럼 보인다.
	 *
	 * <p>dev 실측(2026-08-11): 주말 {@code trading_date} 4일 중 <b>2일(08-08 토·08-09 일)이 뉴스
	 * 런만 있고 skip 행이 0</b> 이었다. 그래서 이 픽스처가 그 형태를 그대로 만든다 — 주말에 뉴스
	 * 런만 두고 skip 행은 안 둔다.
	 */
	@Test
	void 시장_런이_안_돈_주말도_기준_표본에서_빠진다() {
		insertTradingDay("2026-08-03");          // 월
		insertTradingDay("2026-07-31");          // 금 — 유일한 정상 표본
		// 08-01(토)·08-02(일)에 뉴스 런만 있다. skip 행이 없어 원장은 휴장이라고 말하지 않는다.
		insertRun("r-news-0801", "news:2026-08-01T15:30", "news", "SUCCEEDED", "2026-08-01",
				"2026-08-01T06:30:00Z", "2026-08-01T06:30:00Z", null);
		insertRun("r-news-0802", "news:2026-08-02T15:30", "news", "SUCCEEDED", "2026-08-02",
				"2026-08-02T06:30:00Z", "2026-08-02T06:30:00Z", null);
		insertTrigger("g1", "2026-07-31", "etf-a");
		insertTrigger("g2", "2026-07-31", "etf-b");

		// 표본 = {07-31: 2} ⇒ 2.0. 주말이 섞이면 {0, 0, 2} 의 중앙값 0.0 이 된다.
		assertThat(repository.facts(DAY).outputs())
				.filteredOn(o -> o.id().equals("o.trig")).singleElement()
				.satisfies(o -> assertThat(o.base()).isEqualTo(2.0d));
	}

	/**
	 * 🔴 <b>주말을 조회하면 장 산출의 기준을 안 준다</b> — 휴장일과 같은 이유이고, 같은 술어가
	 * 답해야 한다. 원장에는 그 주말이 휴장이라는 신호가 없으므로 <b>달력이 답하는 자리</b>다.
	 */
	@Test
	void 주말을_조회하면_장_산출의_기준을_안_준다() {
		LocalDate saturday = LocalDate.parse("2026-08-01");
		insertRun("r-news-0801", "news:2026-08-01T15:30", "news", "SUCCEEDED", "2026-08-01",
				"2026-08-01T06:30:00Z", "2026-08-01T06:30:00Z", null);
		insertTradingDay("2026-07-31");
		insertTrigger("g1", "2026-07-31", "etf-a");

		List<OutputRow> outputs = repository.facts(saturday).outputs();
		assertThat(outputs).filteredOn(o -> o.id().equals("o.trig")).singleElement()
				.satisfies(o -> assertThat(o.base()).isNull());
		/* 뉴스 갈래 **셋 다** 기준을 그대로 받는다 — marketBound 가 가르는 자리다. 한둘만 재면
		 * 나머지 spec 의 플래그를 개별로 뒤집는 변이가 산다(리뷰가 잡았다). 라벨·단위도 함께
		 * 못 박는다 — 그 값들은 소비자 어휘와 1:1 이라 조용히 바뀌면 화면이 딴 이름을 그린다. */
		assertThat(outputs).filteredOn(o -> !o.id().startsWith("o.pub") && !o.id().equals("o.trig"))
				.extracting(OutputRow::id, OutputRow::label, OutputRow::unit, OutputRow::base)
				.containsExactly(
						org.assertj.core.groups.Tuple.tuple("o.doc", "뉴스 문서", "건", 0.0d),
						org.assertj.core.groups.Tuple.tuple("o.asr", "assertion", "건", 0.0d),
						org.assertj.core.groups.Tuple.tuple("o.evt", "source event", "건", 0.0d));
	}

	/**
	 * 🔴 <b>{@code available_at} 축 산출의 날짜는 KST 로 자른다.</b> 뒤 세 산출({@code o.doc}·
	 * {@code o.asr}·{@code o.evt})은 거래일 컬럼이 없어 수집 시각의 KST 날짜로 센다 — UTC 로 자르면
	 * <b>KST 오전 9시 이전 수집분이 전날로 밀린다</b>.
	 *
	 * <p>그래서 픽스처를 정확히 그 경계에 둔다: {@code 2026-08-03T00:30+09:00} 은 KST 로 08-03 이고
	 * UTC 로는 <b>08-02</b> 다. 존을 바꾸는 변이가 여기서 죽는다.
	 *
	 * <p>{@code document_type='NEWS'} 술어도 함께 잰다 — 공시 문서를 같은 날에 둬서, 술어가 빠지면
	 * 그날 값이 2 가 된다.
	 */
	@Test
	void available_at_산출_셋은_KST_날짜로_센다() {
		insertTradingDay(DAY.toString());
		// 세 산출 모두 경계 시각에 둔다 — 각 SQL 의 KST 캐스트가 **따로** 재져야 한다.
		insertDocument("d1", "NEWS", "2026-08-03T00:30:00+09:00");        // KST 08-03 · UTC 08-02
		insertDocument("d2", "DISCLOSURE", "2026-08-03T10:00:00+09:00");  // o.doc 이 안 세는 것
		/* ⚠️ 세 산출의 **건수를 서로 다르게** 둔다(문서 1 · 주장 2 · 이벤트 3) — 같으면 두 SQL 의
		 * 테이블을 맞바꾸는 변이가 통과한다(리뷰가 잡았다). */
		insertAssertion("a1", "d1", "2026-08-03T00:30:00+09:00");
		insertAssertion("a2", "d1", "2026-08-03T00:30:00+09:00");
		insertSourceEvent("e1", "NEWS", "2026-08-03T00:30:00+09:00");
		insertSourceEvent("e2", "NEWS", "2026-08-03T00:30:00+09:00");
		insertSourceEvent("e3", "NEWS", "2026-08-03T00:30:00+09:00");

		List<OutputRow> outputs = repository.facts(DAY).outputs();
		assertThat(outputs).filteredOn(o -> o.id().equals("o.doc")).singleElement()
				.satisfies(o -> {
					assertThat(o.label()).isEqualTo("뉴스 문서");
					assertThat(o.unit()).isEqualTo("건");
					// 🔴 공시 문서는 안 센다 — `document_type='NEWS'` 술어가 빠지면 2 가 된다.
					assertThat(o.today()).isEqualTo(1L);
				});
		assertThat(outputs).filteredOn(o -> o.id().equals("o.asr")).singleElement()
				.satisfies(o -> assertThat(o.today()).isEqualTo(2L));
		assertThat(outputs).filteredOn(o -> o.id().equals("o.evt")).singleElement()
				.satisfies(o -> assertThat(o.today()).isEqualTo(3L));
	}

	/**
	 * 🔴 <b>{@code o.asr}·{@code o.evt} 는 공시도 함께 센다</b> — {@code o.doc} 과 다르다.
	 * 그 둘의 SQL 에는 계열 술어가 없고, 원장에는 공시 주장·이벤트가 실제로 들어온다
	 * ({@code steps/assemble_disclosure_events.py}). dev 실측(2026-08-11)으로는 <b>주장 0.07%</b>
	 * (63/86,197) · <b>이벤트 0.16%</b>(63/38,543)라 판정을 뒤집지 않지만, <b>공시 레인이 커지면
	 * 달라진다</b>. ⚠️ 이 수치는 저장소만으로 재현되지 않는다 — 근거로 쓸 때 다시 재라.
	 *
	 * <p>여기서 술어를 더하지 않는 이유는 그게 <b>지표의 정의를 바꾸는 일</b>이라서다(라벨도
	 * "assertion"·"source event" 로 계열을 안 밝힌다). 대신 <b>현재 셈법을 못 박아</b> 조용히
	 * 바뀌지 않게 한다 — 정의를 바꾸려면 이 테스트가 먼저 깨져야 한다.
	 */
	@Test
	void assertion_과_source_event_는_공시도_함께_센다() {
		insertTradingDay(DAY.toString());
		insertDocument("d1", "NEWS", "2026-08-03T10:00:00+09:00");
		insertDocument("d2", "DISCLOSURE", "2026-08-03T10:00:00+09:00");
		insertAssertion("a1", "d1", "2026-08-03T10:00:00+09:00");
		insertAssertion("a2", "d2", "2026-08-03T10:00:00+09:00");   // 공시 주장
		insertSourceEvent("e1", "NEWS", "2026-08-03T10:00:00+09:00");
		insertSourceEvent("e2", "DISCLOSURE", "2026-08-03T10:00:00+09:00");

		List<OutputRow> outputs = repository.facts(DAY).outputs();
		assertThat(outputs).filteredOn(o -> o.id().equals("o.asr")).singleElement()
				.satisfies(o -> assertThat(o.today()).isEqualTo(2L));
		assertThat(outputs).filteredOn(o -> o.id().equals("o.evt")).singleElement()
				.satisfies(o -> assertThat(o.today()).isEqualTo(2L));
		// 대비: 문서 산출은 뉴스만 센다 — 셋이 같은 계열이 아니라는 것이 이 대비의 요점이다.
		assertThat(outputs).filteredOn(o -> o.id().equals("o.doc")).singleElement()
				.satisfies(o -> assertThat(o.today()).isEqualTo(1L));
	}

	/**
	 * 🔴 <b>{@code o.pub} 은 게시된 것만 센다.</b> {@code publication_status} 술어는 이 산출에만
	 * 있고 {@code o.trig} 에는 없다 — 빠지면 초안·철회분이 "게시 ETF" 로 집계돼 산출이 부풀고,
	 * 그 위에 세운 기준도 함께 부푼다.
	 *
	 * <p>{@code count(DISTINCT etf_instrument_id)} 도 함께 잰다 — 같은 종목의 게시 둘을 넣는다.
	 */
	@Test
	void 게시_산출은_게시된_것만_종목_단위로_센다() {
		insertTradingDay(DAY.toString());
		insertPublished("p1", DAY.toString(), "etf-a", "PUBLISHED");
		insertPublished("p2", DAY.toString(), "etf-a", "PUBLISHED");   // 같은 종목 → 여전히 1종
		insertPublished("p3", DAY.toString(), "etf-b", "DRAFT");       // 게시 아님
		insertPublished("p4", DAY.toString(), "etf-c", "WITHDRAWN");   // 철회

		assertThat(repository.facts(DAY).outputs())
				.filteredOn(o -> o.id().equals("o.pub")).singleElement()
				.satisfies(o -> {
					assertThat(o.label()).isEqualTo("게시 ETF");
					assertThat(o.unit()).isEqualTo("종");
					assertThat(o.today()).isEqualTo(1L);
				});
	}

	/**
	 * 🔴 <b>표본도 창과 같은 식({@code RUN_DAY})으로 날을 묻는다.</b> 조회 창은 거래일이 없는 런을
	 * 계획 시각의 KST 날짜로 줍는데(위 테스트), 표본만 {@code trading_date} 를 직접 보면 <b>그 런은
	 * 창에는 들어오는데 표본에는 안 잡힌다</b> — 같은 원장을 두 축으로 세는 것이고, 그 파일의
	 * {@code RUN_DAY} 자바독이 경고하는 바로 그 자리다(서브에이전트 리뷰가 짚었다).
	 *
	 * <p>⚠️ 프로듀서는 {@code trading_date} 를 늘 채운다({@code plan_run}) — dev 실측 NULL 0건.
	 * 그래서 이 테스트가 겨누는 것은 <b>운영 상태가 아니라 두 조회의 축이 같은가</b>이고, 그 계약이
	 * 없으면 위 통일이 어떤 단언으로도 안 재진다.
	 */
	@Test
	void 표본도_창과_같은_식으로_날을_묻는다() {
		insertTradingDay(DAY.toString());
		/* 거래일 없는 런 — 계획 시각의 KST 날짜(07-31)가 이 런의 날이다.
		 * 08-01T00:30Z = KST 08-01 09:30 이 아니라, 07-31T21:30Z = KST 08-01 06:30 …
		 * 헷갈리지 않게 KST 로 07-31 이 되는 시각을 쓴다: 07-31T05:00Z = KST 07-31 14:00. */
		insertRun("r-nodate", "news:2026-07-31T14:00", "news", "SUCCEEDED", null,
				"2026-07-31T05:00:00Z", "2026-07-31T05:00:00Z", null);
		insertTrigger("g1", "2026-07-31", "etf-a");
		insertTrigger("g2", "2026-07-31", "etf-b");

		/* 07-31 이 표본에 들면 중앙값 2.0. `trading_date` 만 보면 그 런이 안 잡혀 표본이 비고
		 * base 는 null 이 된다. */
		assertThat(repository.facts(DAY).outputs())
				.filteredOn(o -> o.id().equals("o.trig")).singleElement()
				.satisfies(o -> assertThat(o.base()).isEqualTo(2.0d));
	}

	/**
	 * 경계 축의 세 수. <b>게시했는데 발번이 없다</b> · <b>발번했는데 지금 비게시다</b> · 발번 전체.
	 *
	 * <p>⚠️ 셋을 <b>서로 다른 값</b>으로 만든다 — 같으면 SQL 셋을 맞바꾸는 변이가 통과한다.
	 */
	@Test
	void 경계_축은_게시와_발번이_어긋난_건수를_낸다() {
		long t1 = insertTenant("증권사A");
		insertPublished("p-ok", DAY.toString(), "etf-a", "PUBLISHED");    // 발번 있음 → 안 셈
		insertPublished("p-ok2", DAY.toString(), "etf-d", "PUBLISHED");   // 〃
		deliverNew(t1, "p-ok");
		deliverNew(t1, "p-ok2");

		/* 🔴 게시했는데 발번 없음 → 3건. **셋을 같은 ETF 로 둔다** — 다르게 두면 이 수를
		 * `count(DISTINCT etf_instrument_id)` 로 바꾸는 변이가 통과하고, 운영에선 한 ETF 에 as-of
		 * 다른 설명이 하루 여럿이라 **축소 보고**된다(변이 리뷰가 잡았다). */
		insertPublished("p-nodev1", DAY.toString(), "etf-b", "PUBLISHED");
		insertPublished("p-nodev2", DAY.toString(), "etf-b", "PUBLISHED");
		insertPublished("p-nodev3", DAY.toString(), "etf-b", "PUBLISHED");

		/* 🔴 **게시본이 아닌 무발번은 안 센다** — 초안·철회는 애초에 발번 대상이 아니라 무발번이
		 * 정상이다. 둘 다 둬야 술어를 `true` 로 여는 변이와 `IN ('PUBLISHED','WITHDRAWN')` 로
		 * 넓히는 변이가 **각각** 죽는다. 안 그러면 이 수가 초안 수만큼 상시 올라 절대 0 이 안 된다. */
		insertPublished("p-draft", DAY.toString(), "etf-f", "DRAFT");
		insertPublished("p-wd", DAY.toString(), "etf-g", "WITHDRAWN");

		/* 🔴 발번했는데 지금 비게시 → 2건. **비게시가 WITHDRAWN 뿐이 아니다** — 초안으로 되돌아간
		 * 것도 테넌트는 페이로드를 들고 있으므로 위반이다. 철회만 두면 `<> 'PUBLISHED'` 를
		 * `= 'WITHDRAWN'` 으로 좁히는 변이가 통과한다(0 이 사실이 아닌데 0 으로 보이는 방향). */
		insertPublished("p-gone", DAY.toString(), "etf-c", "PUBLISHED");
		deliverNew(t1, "p-gone");
		withdraw("p-gone");
		insertPublished("p-back", DAY.toString(), "etf-h", "PUBLISHED");
		deliverNew(t1, "p-back");
		draft("p-back");

		// ⚠️ 셋을 **서로 다른 값**으로 둔다 — 같으면 와이어/서브쿼리를 맞바꾸는 변이가 통과한다.
		assertThat(repository.facts(DAY).boundary()).satisfies(b -> {
			assertThat(b.publishedWithoutDelivery()).isEqualTo(3L);   // p-nodev1·2·3
			assertThat(b.deliveryNowNonpublished()).isEqualTo(2L);    // p-gone · p-back
			assertThat(b.deliveryRows()).isEqualTo(4L);               // NEW 넷
		});
	}

	/**
	 * 🔴 <b>테넌트가 하나도 없으면 미발번은 위반이 아니다.</b> {@code _fanout_new} 는 {@code tenant}
	 * 전건에 발번하므로 0명이면 0행이고, 호출부는 그걸 <b>정상 성공</b>으로 기록한다
	 * ({@code fanout_tenants: 0}). 안 막으면 새 환경 부트스트랩에서 게시본 전건이 위반으로 선다.
	 */
	@Test
	void 테넌트가_없으면_미발번은_위반이_아니다() {
		insertPublished("p-ok", DAY.toString(), "etf-a", "PUBLISHED");   // 테넌트 0명

		assertThat(repository.facts(DAY).boundary().publishedWithoutDelivery()).isZero();

		/* 테넌트가 생기는 순간 같은 행이 위반으로 선다 — 가드가 그 경계에 있다.
		 * ⚠️ **DEV·ACTIVE 가 아닌 테넌트**로 만든다: `_fanout_new` 는 필터 없이 전건에 발번하므로
		 * 가드도 전건이어야 하고, 여기서 DEV·ACTIVE 를 쓰면 가드를 좁히는 변이가 통과한다. */
		insertTenant("증권사A", "PROD", "ONBOARDING");
		assertThat(repository.facts(DAY).boundary().publishedWithoutDelivery()).isEqualTo(1L);
	}

	/**
	 * 🔴 <b>정상 무효화는 위반이 아니다.</b> 운영자 무효화는 결과를 WITHDRAWN 으로 전이하고 그 NEW 를
	 * 받은 테넌트에 INVALIDATION 을 발번하되 <b>원래 NEW 행을 남긴다</b>
	 * ({@code JdbcAnalysisWriteRepository.invalidate}). 상태만 보면 "발번했는데 현재 비게시"라
	 * 정상 무효화한 분석이 전부 위반으로 남아 그 수가 <b>영구히 단조 증가</b>한다.
	 *
	 * <p>🔴 그리고 상관은 결과만이 아니라 <b>테넌트까지</b>다 — 무효화는 그 NEW 를 받은 테넌트에만
	 * 나가므로, <b>받고도 통지 못 받은 테넌트</b>가 있으면 그건 진짜 위반이다. 그래서 테넌트 둘을
	 * 두고 한쪽에만 통지한다: 결과 단위로만 상관시키면 이 픽스처가 <b>0건</b>으로 나온다.
	 */
	@Test
	void 무효화_통지가_간_발번은_비게시로_안_센다() {
		long a = insertTenant("증권사A");
		long b = insertTenant("증권사B");

		// ① A·B 가 받았고 A 만 통지받았다 → B 의 행이 위반.
		insertPublished("p-inv", DAY.toString(), "etf-a", "PUBLISHED");
		deliverNew(a, "p-inv");
		deliverNew(b, "p-inv");
		withdraw("p-inv");
		deliverInvalidation(a, "p-inv");

		/* ② 🔴 **B 에게 "다른 결과"의 무효화를 준다.** 면제가 "그 테넌트가 아무 무효화나 한 번
		 * 받았으면"으로 미끄러지면 ①의 B 행이 조용히 면제된다 — 운영 며칠이면 모든 테넌트가
		 * 무효화를 한 번씩 받으므로 그 뒤로는 이 수가 **영구히 0** 이고 콘솔은 "정합"을 계속
		 * 띄운다(변이 리뷰가 잡았다 — 위반을 숨기는 방향이라 가장 나쁘다). */
		insertPublished("p-other", DAY.toString(), "etf-b", "PUBLISHED");
		deliverNew(b, "p-other");
		withdraw("p-other");
		deliverInvalidation(b, "p-other");   // B 는 이 결과에 대해서는 정상

		/* ③ 통지가 아예 없는 철회 — A·B 둘 다 위반. 이걸로 위반 행이 **셋**이 되고
		 * (b/p-inv · a/p-third · b/p-third) 서로 다른 테넌트 2 · 서로 다른 결과 2 라,
		 * `count(*)` 를 테넌트나 결과 단위로 바꾸는 변이가 **각각** 죽는다. 행 수로 안 세면
		 * 팬아웃 사고가 축소 보고된다(한 결과가 20개 테넌트에 나가 있어도 1 로 뜬다). */
		insertPublished("p-third", DAY.toString(), "etf-c", "PUBLISHED");
		deliverNew(a, "p-third");
		deliverNew(b, "p-third");
		withdraw("p-third");

		assertThat(repository.facts(DAY).boundary()).satisfies(x -> {
			assertThat(x.deliveryNowNonpublished()).isEqualTo(3L);
			assertThat(x.deliveryRows()).isEqualTo(7L);   // NEW 다섯 + INVALIDATION 둘
		});
	}

	/**
	 * 🔴 <b>{@code deliveryRows} 는 "발번이 돌고는 있나"를 답한다.</b> 앞의 둘이 0 일 때 그것이
	 * <b>정합</b>인지 <b>발번이 아직 하나도 없음</b>인지 이 값이 가른다 — 없으면 "아무 문제 없음"과
	 * "아직 아무것도 안 나감"이 화면에서 같은 칸이 된다.
	 */
	@Test
	void 발번이_하나도_없는_것과_정합인_것을_가른다() {
		assertThat(repository.facts(DAY).boundary()).satisfies(b -> {
			assertThat(b.publishedWithoutDelivery()).isZero();
			assertThat(b.deliveryNowNonpublished()).isZero();
			assertThat(b.deliveryRows()).isZero();
		});

		long t = insertTenant("증권사A");
		insertPublished("p-ok", DAY.toString(), "etf-a", "PUBLISHED");
		deliverNew(t, "p-ok");

		// 위반은 여전히 0 인데 분모가 1 — 이제 "정합"이라고 말할 수 있다.
		assertThat(repository.facts(DAY).boundary()).satisfies(b -> {
			assertThat(b.publishedWithoutDelivery()).isZero();
			assertThat(b.deliveryNowNonpublished()).isZero();
			assertThat(b.deliveryRows()).isEqualTo(1L);
		});
	}

	/**
	 * ⚠️ <b>경계 축만 날짜 창을 안 탄다.</b> 나머지 축은 "그 날 무슨 일이 있었나"이고 이건 "지금
	 * 어긋난 것이 몇 건인가"라 누적이다 — 날짜로 자르면 조회한 날에 안 생긴 위반이 화면에서 사라지고,
	 * 어제 난 사고가 오늘 조회에서 저절로 나은 것처럼 보인다.
	 */
	@Test
	void 경계_축은_조회한_날과_무관하게_누적을_낸다() {
		long t = insertTenant("증권사A");
		// 조회 날(08-03)과 **다른 날**의 게시본이 발번 없이 남아 있다.
		insertPublished("p-old", "2026-07-31", "etf-a", "PUBLISHED");
		/* 🔴 **둘째 수도 함께 잰다.** 첫째만 단언하면 둘째 서브쿼리에 날짜 창을 붙이는 변이가
		 * 통과하고, 그러면 통지 없이 철회된 발번이 **다음 날 화면에서 사라진다** — 이 축의 존재
		 * 이유로 내건 문장("어제 어긋난 것이 오늘 저절로 낫지 않는다")이 정작 그 수에서 깨진다
		 * (변이 리뷰가 잡았다). */
		insertPublished("p-old-gone", "2026-07-31", "etf-b", "PUBLISHED");
		deliverNew(t, "p-old-gone");
		withdraw("p-old-gone");

		assertThat(repository.facts(DAY).boundary()).satisfies(b -> {
			assertThat(b.publishedWithoutDelivery()).isEqualTo(1L);
			assertThat(b.deliveryNowNonpublished()).isEqualTo(1L);
		});
		// 그 날을 직접 조회해도 같은 수다 — 창이 없다는 뜻이다.
		assertThat(repository.facts(LocalDate.parse("2026-07-31")).boundary()).satisfies(b -> {
			assertThat(b.publishedWithoutDelivery()).isEqualTo(1L);
			assertThat(b.deliveryNowNonpublished()).isEqualTo(1L);
		});
	}

	/** 원장이 비면 DB 시계의 KST 오늘 — 날짜가 없으면 화면이 "무엇을 본 응답인가"를 못 말한다. */
	@Test
	void 원장이_비면_DB_시계의_KST_오늘을_본다() {
		assertThat(repository.facts(null).today())
				.isEqualTo(jdbc.queryForObject(
						"SELECT (now() AT TIME ZONE 'Asia/Seoul')::date", LocalDate.class));
	}
}
