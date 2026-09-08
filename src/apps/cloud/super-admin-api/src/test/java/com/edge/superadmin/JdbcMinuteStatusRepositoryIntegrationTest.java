package com.edge.superadmin;

import com.edge.superadmin.repository.MinuteStatusRepository;
import com.edge.superadmin.repository.MinuteStatusRepository.MinuteStatus;
import com.edge.superadmin.repository.MinuteStatusRepository.SessionSummary;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 1분 요약 SQL 통합 테스트(ALPHA-651) — 실 스키마에서 <b>무증거 파생</b>을 잠근다.
 * MISSING 은 EOD QC 가 매기므로 장중에 실행체가 죽으면 창은 DUE 로 잔류한다 — 이 테스트가
 * 깨지면(예: 파생을 MISSING 집계로 "단순화") 죽은 실행체가 결손 0 으로 보이는 회귀다(Rule 9).
 */
@Transactional
class JdbcMinuteStatusRepositoryIntegrationTest extends CloudPostgresIntegrationTest {

	private static final LocalDate DAY = LocalDate.of(2026, 8, 3);
	// overdue·lease 판정 축은 DB now() 다 — 고정 시각은 달력이 지나면 미래/과거가 뒤집혀
	// 테스트가 시한폭탄이 된다. ±2시간 상대 시각이면 판정 방향이 실행 시점과 무관하다.
	private static final OffsetDateTime PAST =
			OffsetDateTime.now(ZoneOffset.UTC).minusHours(2).withNano(0);

	@Autowired
	private MinuteStatusRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	@Test
	void 기한_지난_DUE와_CLAIMED는_MISSING_판정_전이라도_무증거로_센다() {
		insertSession("sess-p", "price_minute", "toss", DAY, "ACTIVE");
		// 과거 창 6: 무증거 DUE(죽은 실행체의 흔적) · 무증거 CLAIMED(lease NULL — claim 흔적만
		// 있고 유효 lease 없음, DUE 만 세면 이 유형이 사라진다) · live lease CLAIMED(방금 닫힌
		// 창을 정상 수집 중 — 무증거로 세면 매분 오탐, 봇 P2) · 빈 데이터(VALID_EMPTY) · VALID ·
		// window_end는 지났지만 scheduled_at이 아직 안 온 DUE(종가 단일가 지연).
		// 미래 창 1: DUE 지만 아직 기한 전 — 무증거로 세면 정상 진행이 상시 결함이 된다.
		insertWindow("sess-p", PAST, "DUE");
		insertWindow("sess-p", PAST.plusMinutes(1), "CLAIMED"); // lease NULL
		insertWindow("sess-p", PAST.plusMinutes(2), "CLAIMED");
		jdbc.update("""
				UPDATE minute_ingestion_window SET lease_expires_at = now() + interval '5 minutes'
				WHERE session_id = 'sess-p' AND window_start = ?
				""", PAST.plusMinutes(2));
		insertWindow("sess-p", PAST.plusMinutes(3), "VALID_EMPTY");
		insertWindow("sess-p", PAST.plusMinutes(4), "VALID");
		insertWindow("sess-p", PAST.plusMinutes(5), "DUE");
		jdbc.update("""
				UPDATE minute_ingestion_window SET scheduled_at = now() + interval '2 hours'
				WHERE session_id = 'sess-p' AND window_start = ?
				""", PAST.plusMinutes(5));
		insertWindow("sess-p", OffsetDateTime.now(ZoneOffset.UTC).plusHours(2), "DUE");

		SessionSummary s = repository.status(DAY).sessions().get(0);

		assertThat(s.windows().due()).isEqualTo(3);
		assertThat(s.windows().claimed()).isEqualTo(2);
		assertThat(s.windows().overdueNoEvidence()).isEqualTo(2);
		assertThat(s.windows().validEmpty()).isEqualTo(1);
		assertThat(s.windows().missing()).isZero();
		// 근거 목록엔 무증거 창만 있다 — 기한 전 DUE·VALID_EMPTY·live claim 은 결손이 아니다.
		assertThat(s.gaps()).hasSize(2);
		// JDBC 가 돌려주는 offset(세션 TZ)은 계약이 아니다 — 시각(instant)만 단언한다.
		assertThat(s.gaps().get(0).windowStart().toInstant()).isEqualTo(PAST.toInstant());
		assertThat(s.gaps()).allSatisfy(g -> assertThat(g.noEvidence()).isTrue());
	}

	@Test
	void 허용된_실패가_있는_VALID_창도_문제창에_표시한다() {
		insertSession("sess-partial", "price_minute", "kis", DAY, "ACTIVE");
		insertWindow("sess-partial", PAST, "VALID");
		insertWindow("sess-partial", PAST.plusMinutes(1), "VALID");
		jdbc.update("""
				UPDATE minute_ingestion_window SET failed_unit_count = 0
				WHERE session_id = 'sess-partial' AND window_start = ?
				""", PAST.plusMinutes(1));
		insertSession("sess-other-day", "price_minute", "kis", DAY.plusDays(1), "ACTIVE");
		insertWindow("sess-other-day", PAST, "VALID");
		jdbc.update("""
				UPDATE minute_ingestion_window
				SET expected_unit_count = 462, succeeded_unit_count = 461,
				    failed_unit_count = 1, missing_units = '["0220W0"]'::jsonb
				WHERE session_id = 'sess-partial' AND window_start = ?
				""", PAST);
		jdbc.update("""
				UPDATE minute_ingestion_window SET failed_unit_count = 1
				WHERE session_id = 'sess-other-day' AND window_start = ?
				""", PAST);

		SessionSummary summary = repository.status(DAY).sessions().get(0);

		assertThat(summary.gaps()).hasSize(1);
		assertThat(summary.gaps().get(0).windowStart().toInstant()).isEqualTo(PAST.toInstant());
		assertThat(summary.gaps().get(0).dataStatus()).isEqualTo("VALID");
		assertThat(summary.gaps().get(0).noEvidence()).isFalse();
	}

	@Test
	void lease_부재는_null_만료는_true_로_구분한다() {
		// WHY: PLANNED(기동 증거 없음)와 ACTIVE+만료(증거 끊김)는 다른 사실이다 — 뭉개면
		//      "아직 안 뜬 세션"과 "죽은 세션"이 화면에서 같아진다.
		insertSession("sess-n", "price_minute", "toss", DAY, "PLANNED");
		insertSession("sess-e", "news_minute", "bigkinds", DAY, "ACTIVE");
		jdbc.update("UPDATE minute_ingestion_session SET lease_expires_at = ? WHERE session_id = 'sess-e'",
				PAST);

		MinuteStatus status = repository.status(DAY);

		SessionSummary planned = status.sessions().stream()
				.filter(s -> s.sessionId().equals("sess-n")).findFirst().orElseThrow();
		SessionSummary expired = status.sessions().stream()
				.filter(s -> s.sessionId().equals("sess-e")).findFirst().orElseThrow();
		assertThat(planned.leaseExpired()).isNull();
		assertThat(expired.leaseExpired()).isTrue();
	}

	@Test
	void 날짜_필터가_다른_날_세션과_job_을_걸러낸다() {
		insertSession("sess-t", "price_minute", "toss", DAY, "ACTIVE");
		insertSession("sess-x", "price_minute", "toss", DAY.plusDays(1), "PLANNED");
		insertWindow("sess-t", PAST, "VALID");
		insertPriceJob("job-t", "sess-t", PAST, "DEAD");
		insertWindow("sess-t", PAST.plusMinutes(3), "VALID");
		insertPriceJob("job-stale", "sess-t", PAST.plusMinutes(3), "DEAD");
		jdbc.update("UPDATE price_window_job SET error_code = 'STALE' WHERE job_id = 'job-stale'");
		insertWindow("sess-t", PAST.plusMinutes(4), "VALID");
		insertPriceJob("job-backfill", "sess-t", PAST.plusMinutes(4), "PENDING");
		insertWindow("sess-t", PAST.plusMinutes(5), "VALID");
		insertPriceJob("job-realtime", "sess-t", PAST.plusMinutes(5), "PENDING");
		insertPriceOutbox("job-realtime", 0, "NEW");
		insertWindow("sess-t", PAST.plusMinutes(6), "VALID");
		insertPriceJob("job-delivery-dead", "sess-t", PAST.plusMinutes(6), "PENDING");
		insertPriceOutbox("job-delivery-dead", 0, "DEAD");
		insertWindow("sess-t", PAST.plusMinutes(7), "VALID");
		insertPriceJob("job-redrive", "sess-t", PAST.plusMinutes(7), "RETRY_WAIT");
		jdbc.update("UPDATE price_window_job SET redrive_generation = 1 WHERE job_id = 'job-redrive'");
		insertPriceOutbox("job-redrive", 0, "DEAD");
		insertPriceOutbox("job-redrive", 1, "NEW");
		// 유효 lease 없는 CLAIMED job 두 형태(만료·NULL) — writer 의 회수 조건(IS NULL OR
		// < now())과 같은 집합이어야 한다. NULL 을 빼면 그 고착이 "처리 중"으로 숨는다.
		insertWindow("sess-t", PAST.plusMinutes(1), "VALID");
		insertPriceJob("job-c", "sess-t", PAST.plusMinutes(1), "CLAIMED");
		jdbc.update("UPDATE price_window_job SET lease_expires_at = ? WHERE job_id = 'job-c'",
				PAST);
		insertWindow("sess-t", PAST.plusMinutes(2), "VALID");
		insertPriceJob("job-n", "sess-t", PAST.plusMinutes(2), "CLAIMED"); // lease NULL
		// 뉴스 job 은 created_at 의 KST 날짜 축 — 반개구간 경계 자체를 밟는다: 8/3 00:00:00
		// KST 정각(= 8/2 15:00 UTC)은 포함(>=), 8/4 00:00:00 KST 정각은 배제(<)여야 한다.
		// 두 경계의 status 를 다르게 둔다 — 둘 다 DEAD 면 >/<= 쌍 회귀(하한 누락+상한 포함)가
		// 합계를 보존해 통과한다(리뷰 3라운드).
		insertNewsJob("nj-in", "2026-08-02T15:00:00Z", "DEAD");
		insertNewsJob("nj-delivery", "2026-08-02T16:00:00Z", "PENDING");
		insertNewsOutbox("nj-delivery", 0, "DEAD");
		insertNewsJob("nj-out", "2026-08-02T14:50:00Z", "SUCCEEDED"); // = 8/2 23:50 KST
		insertNewsJob("nj-next", "2026-08-03T15:00:00Z", "SUCCEEDED"); // = 8/4 00:00 KST 정각

		MinuteStatus status = repository.status(DAY);

		assertThat(status.sessions()).extracting(SessionSummary::sessionId)
				.containsExactly("sess-t");
		// 정정으로 폐기된 이전 세대 STALE 은 정상 격리다. 실제 DEAD 와 합치면 정정한 날이
		// 영구 주의로 남는다.
		assertThat(status.sessions().get(0).priceJobs().dead()).isEqualTo(1);
		assertThat(status.sessions().get(0).priceJobs().deliveryFailed()).isEqualTo(1);
		assertThat(status.sessions().get(0).priceJobs().claimed()).isEqualTo(2);
		assertThat(status.sessions().get(0).priceJobs().claimedExpired()).isEqualTo(2);
		assertThat(status.sessions().get(0).priceJobs().waiting()).isEqualTo(2);
		assertThat(status.newsJobs().dead()).isEqualTo(1);
		assertThat(status.newsJobs().deliveryFailed()).isEqualTo(1);
		assertThat(status.newsJobs().succeeded()).isZero();
	}

	@Test
	void 오늘_실시간_price_job의_필수_event_부재는_전달실패로_센다() {
		LocalDate today = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
		insertSession("sess-today", "price_minute", "toss", today, "ACTIVE");
		insertWindow("sess-today", PAST, "VALID");
		insertPriceJob("job-missing-event", "sess-today", PAST, "PENDING");

		SessionSummary session = repository.status(today).sessions().get(0);

		assertThat(session.priceJobs().waiting()).isZero();
		assertThat(session.priceJobs().deliveryFailed())
				.as("실시간 job은 outbox가 없으면 consumer에 도달할 수 없다")
				.isEqualTo(1);
	}

	@Test
	void 세션_부재는_빈_목록이다() {
		MinuteStatus status = repository.status(DAY);
		assertThat(status.sessions()).isEmpty();
	}

	@Test
	void 일별_범위는_양끝과_KST_job_경계를_포함하고_범위밖은_제외한다() {
		LocalDate from = DAY.minusDays(1);
		LocalDate to = DAY.plusDays(1);
		insertSession("sess-before", "price_minute", "kis", from.minusDays(1), "FAILED");
		insertSession("sess-from", "price_minute", "kis", from, "FAILED");
		insertSession("sess-to", "price_minute", "toss", to, "ACTIVE");
		insertSession("sess-after", "price_minute", "kis", to.plusDays(1), "FAILED");
		insertWindow("sess-from", PAST, "VALID");
		jdbc.update("UPDATE minute_ingestion_window SET failed_unit_count = 1 WHERE session_id = 'sess-from'");
		insertPriceJob("daily-dead", "sess-from", PAST, "DEAD");
		insertWindow("sess-from", PAST.plusMinutes(2), "VALID");
		insertPriceJob("daily-stale", "sess-from", PAST.plusMinutes(2), "DEAD");
		jdbc.update("UPDATE price_window_job SET error_code = 'STALE' WHERE job_id = 'daily-stale'");
		insertWindow("sess-from", PAST.plusMinutes(3), "VALID");
		insertPriceJob("daily-backfill", "sess-from", PAST.plusMinutes(3), "PENDING");
		insertWindow("sess-from", PAST.plusMinutes(4), "VALID");
		insertPriceJob("daily-realtime", "sess-from", PAST.plusMinutes(4), "PENDING");
		insertPriceOutbox("daily-realtime", 0, "NEW");
		insertWindow("sess-from", PAST.plusMinutes(5), "VALID");
		insertPriceJob("daily-delivery-dead", "sess-from", PAST.plusMinutes(5), "PENDING");
		insertPriceOutbox("daily-delivery-dead", 0, "DEAD");
		insertWindow("sess-to", PAST.plusMinutes(1), "VALID_EMPTY");

		// from 00:00 KST 는 포함, to+1 00:00 KST 는 제외한다. 날짜별 GROUP BY 도 함께 잠근다.
		insertNewsJob("daily-in", "2026-08-01T15:00:00Z", "DEAD");
		insertNewsJob("daily-delivery", "2026-08-01T16:00:00Z", "PENDING");
		insertNewsOutbox("daily-delivery", 0, "DEAD");
		insertNewsJob("daily-middle", "2026-08-02T15:00:00Z", "SUCCEEDED");
		insertNewsJob("daily-out", "2026-08-04T15:00:00Z", "DEAD");

		MinuteStatusRepository.DailyStatus daily = repository.dailyStatus(from, to);

		assertThat(daily.sessions()).extracting(MinuteStatusRepository.DailySessionSummary::sessionDate)
				.containsExactly(from, to);
		assertThat(daily.sessions().get(0).windows().failedUnitWindows()).isEqualTo(1);
		assertThat(daily.sessions().get(0).priceJobs().dead()).isEqualTo(1);
		assertThat(daily.sessions().get(0).priceJobs().deliveryFailed()).isEqualTo(1);
		assertThat(daily.sessions().get(0).priceJobs().waiting()).isEqualTo(1);
		assertThat(daily.sessions().get(1).windows().validEmpty()).isEqualTo(1);
		assertThat(daily.newsJobs()).containsOnlyKeys(from, DAY);
		assertThat(daily.newsJobs().get(from).dead()).isEqualTo(1);
		assertThat(daily.newsJobs().get(from).deliveryFailed()).isEqualTo(1);
		assertThat(daily.newsJobs().get(DAY).succeeded()).isEqualTo(1);
	}

	private void insertSession(String id, String dataset, String sourceGroup, LocalDate date,
			String phase) {
		jdbc.update("""
				INSERT INTO minute_ingestion_session (session_id, dataset, source_group,
				       session_date, universe_version, universe_hash, phase, expected_window_count)
				VALUES (?, ?, ?, ?, 'uv-1', 'hash-1', ?, 390)
				""", id, dataset, sourceGroup, date, phase);
	}

	private void insertWindow(String sessionId, OffsetDateTime start, String status) {
		jdbc.update("""
				INSERT INTO minute_ingestion_window (session_id, window_start, window_end,
				       scheduled_at, data_status)
				VALUES (?, ?, ?, ?, ?)
				""", sessionId, start, start.plusMinutes(1), start, status);
	}

	private void insertPriceJob(String jobId, String sessionId, OffsetDateTime windowStart,
			String status) {
		// FK 가 window 행을 요구한다 — job 이 window 를 앞설 수 없다(스키마 주석).
		jdbc.update("UPDATE minute_ingestion_window SET generation = 1 WHERE session_id = ? AND window_start = ?",
				sessionId, windowStart);
		jdbc.update("""
				INSERT INTO price_window_job (job_id, session_id, window_start, generation,
				       trigger_schema_version, status)
				VALUES (?, ?, ?, 1, 'v1', ?)
				""", jobId, sessionId, windowStart, status);
	}

	private void insertNewsJob(String jobId, String createdAtUtc, String status) {
		jdbc.update("""
				INSERT INTO news_extraction_job (job_id, source_code, article_id,
				       input_fingerprint, tagger_version, ontology_version, status, created_at)
				VALUES (?, 'BIGKINDS', ?, 'fp', 'tg-1', 'on-1', ?, ?::timestamptz)
				""", jobId, jobId, status, createdAtUtc);
	}

	private void insertPriceOutbox(String jobId, int redriveGeneration, String status) {
		insertOutbox("PriceWindowCommitted", jobId, redriveGeneration,
				"price-analysis-realtime", status);
	}

	private void insertNewsOutbox(String jobId, int redriveGeneration, String status) {
		insertOutbox("NewsExtractionRequested", jobId, redriveGeneration,
				"news-extraction-realtime", status);
	}

	private void insertOutbox(String eventType, String jobId, int redriveGeneration,
			String destination, String status) {
		jdbc.update("""
				INSERT INTO dataset_commit_outbox (event_id, event_type, destination,
				       aggregate_id, generation, payload, status)
				VALUES (?, ?, ?, ?, 1, '{}'::jsonb, ?)
				""", eventType + ":" + jobId + ":" + redriveGeneration,
				eventType, destination, jobId, status);
	}
}
