package com.edge.superadmin.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * {@link MinuteStatusRepository} 의 JdbcTemplate 구현(ALPHA-651).
 *
 * <p>시계는 전부 DB {@code now()} 다 — overdue/lease 판정을 앱 시계로 하면 커밋을 쓰는
 * 실행체(DB 시계 기준)와 판정 축이 어긋난다. 네 조회는 한 REPEATABLE READ 스냅샷에서 돈다
 * (드릴다운 네 조회와 같은 이유 — 조회 사이에 writer 가 끼면 "집계엔 없는 창이 목록에 있는"
 * 존재한 적 없는 조합이 조립된다).
 */
@Repository
public class JdbcMinuteStatusRepository implements MinuteStatusRepository {

	private static final String SESSIONS_SQL = """
			SELECT session_id, dataset, source_group, session_date, phase, universe_version,
			       expected_window_count, processed_through, contiguous_complete_through,
			       heartbeat_at, lease_expires_at,
			       CASE WHEN lease_expires_at IS NULL THEN NULL
			            ELSE lease_expires_at < now() END AS lease_expired
			  FROM minute_ingestion_session
			 WHERE session_date = ?
			 ORDER BY dataset, source_group, session_id
			""";

	/**
	 * 무증거 술어 — 수집 가능 시각({@code scheduled_at})이 지난 DUE, 또는 <b>유효한 lease 가 없는</b>
	 * CLAIMED(만료·NULL — writer 의 재청구 조건과 동일 집합). live lease 의 CLAIMED 는
	 * 방금 닫힌 창을 정상 수집 중인 상태라 무증거로 세면 매분 오탐이 깜빡인다(봇 P2).
	 * 두 SQL 이 같은 술어를 써야 집계와 근거 목록이 어긋나지 않는다.
	 */
	private static final String NO_EVIDENCE_PREDICATE = """
			((w.data_status = 'DUE'
			  OR (w.data_status = 'CLAIMED'
			      AND (w.lease_expires_at IS NULL OR w.lease_expires_at < now())))
			 AND w.scheduled_at <= now())""";

	/**
	 * {@code overdue_no_evidence}: MISSING 은 EOD QC 가 매기므로 장중의 결손은 이 파생으로만
	 * 보인다 — MISSING 만 세면 죽은 실행체가 결손 0 으로 보인다(인터페이스 주석의 방향 점검).
	 */
	private static final String WINDOW_COUNTS_SQL = """
			SELECT w.session_id,
			       count(*) FILTER (WHERE w.data_status = 'DUE')         AS due,
			       count(*) FILTER (WHERE w.data_status = 'CLAIMED')     AS claimed,
			       count(*) FILTER (WHERE w.data_status = 'VALID')       AS valid,
			       count(*) FILTER (WHERE w.data_status = 'VALID_EMPTY') AS valid_empty,
			       count(*) FILTER (WHERE w.data_status = 'INCOMPLETE')  AS incomplete,
			       count(*) FILTER (WHERE w.data_status = 'MISSING')     AS missing,
			       count(*) FILTER (WHERE w.data_status = 'INVALID')     AS invalid,
			       count(*) FILTER (WHERE
			""" + NO_EVIDENCE_PREDICATE + """
			       ) AS overdue_no_evidence
			  FROM minute_ingestion_window w
			  JOIN minute_ingestion_session s ON s.session_id = w.session_id
			 WHERE s.session_date = ?
			 GROUP BY w.session_id
			""";

	/**
	 * 실패 unit·결손·무증거 창 전량 — 상한을 두지 않는 이유는 창이 장 시작 시 하루치로 materialize 돼
	 * 세션당 최대 수백 행으로 유계이기 때문이다(무한 스캔 아님). 집계만 내리고 목록을 자르면
	 * "표시된 집계를 목록으로 검증할 길"이 끊긴다.
	 */
	private static final String GAPS_SQL = """
			SELECT w.session_id, w.window_start, w.window_end, w.data_status,
			""" + NO_EVIDENCE_PREDICATE + """
			       AS no_evidence
			  FROM minute_ingestion_window w
			  JOIN minute_ingestion_session s ON s.session_id = w.session_id
			 WHERE s.session_date = ?
			   AND (w.data_status IN ('MISSING','INCOMPLETE','INVALID')
			        OR w.failed_unit_count > 0
			        OR
			""" + NO_EVIDENCE_PREDICATE + """
			       )
			 ORDER BY w.session_id, w.window_start
			""";

	// claimed 중 유효한 lease 가 없는 것(NULL 포함)을 따로 센다 — Consumer 사망 고착 후보를
	// "처리 중"에 뭉개지 않기 위해(인터페이스 주석). NULL 을 빼면 안 된다: writer 의 회수
	// 조건이 `IS NULL OR < now()`(jobs.py) 라, NULL 도 원장이 정의한 고착 형태다.
	// 판정 시계는 나머지 파생과 같은 DB now() 다.
	private static final String JOB_COUNT_COLUMNS = """
			count(*) FILTER (WHERE %1$s.status IN ('PENDING','RETRY_WAIT')%2$s) AS waiting,
			count(*) FILTER (WHERE %1$s.status = 'CLAIMED')   AS claimed,
			count(*) FILTER (WHERE %1$s.status = 'CLAIMED'
			                   AND (%1$s.lease_expires_at IS NULL
			                        OR %1$s.lease_expires_at < now())) AS claimed_expired,
			count(*) FILTER (WHERE %1$s.status = 'SUCCEEDED') AS succeeded,
			count(*) FILTER (WHERE %1$s.status = 'DEAD'%3$s) AS dead,
			count(*) FILTER (WHERE %4$s) AS delivery_failed
			""";
	// price 의 DEAD('STALE')은 정정 후 이전 generation을 의도적으로 격리한 정상 귀결이다.
	// outbox 없는 PENDING/RETRY_WAIT은 과거일 백필이 의도적으로 발행하지 않은 job이다.
	// 둘을 실시간 실패·대기와 합치면 정정/백필한 날짜가 영구 경고로 남는다.
	private static final String PRICE_JOB_COUNT_COLUMNS =
			JOB_COUNT_COLUMNS.formatted("j", " AND o.status IN ('NEW','PUBLISHED')",
					" AND j.error_code IS DISTINCT FROM 'STALE'",
					"j.status IN ('PENDING','RETRY_WAIT')"
							+ " AND (o.status = 'DEAD' OR (o.event_id IS NULL"
							+ " AND s.session_date = (now() AT TIME ZONE 'Asia/Seoul')::date))");
	private static final String NEWS_JOB_COUNT_COLUMNS =
			JOB_COUNT_COLUMNS.formatted("j", " AND o.status IN ('NEW','PUBLISHED')",
					"", "j.status IN ('PENDING','RETRY_WAIT')"
							+ " AND (o.event_id IS NULL OR o.status = 'DEAD')");

	private static final String PRICE_JOBS_SQL = """
			SELECT j.session_id,
			""" + PRICE_JOB_COUNT_COLUMNS + """
			  FROM price_window_job j
			  JOIN minute_ingestion_session s ON s.session_id = j.session_id
			  LEFT JOIN dataset_commit_outbox o
			    ON o.event_id = 'PriceWindowCommitted:' || j.job_id || ':' || j.redrive_generation
			 WHERE s.session_date = ?
			 GROUP BY j.session_id
			""";

	/**
	 * 뉴스 job 은 세션과 연결 컬럼이 없다(기사 identity 기반) — 날짜 축은 job 생성 시각의
	 * KST 날짜다. 반개구간 범위 조건이라 인덱스가 생기면 그대로 탄다 — 표현식 캐스트 필터는
	 * 60초 자동 갱신 화면에서 이력 누적분 풀스캔을 반복한다(리뷰 1라운드).
	 */
	private static final String NEWS_JOBS_SQL = """
			SELECT
			""" + NEWS_JOB_COUNT_COLUMNS + """
			  FROM news_extraction_job j
			  LEFT JOIN dataset_commit_outbox o
			    ON o.event_id = 'NewsExtractionRequested:' || j.job_id || ':' || j.redrive_generation
			 WHERE j.created_at >= ? AND j.created_at < ?
			""";

	/** 격자용 범위 집계 — 날짜별 상세 API 호출을 반복하지 않고 세션별 사실을 한 번에 읽는다. */
	private static final String DAILY_SESSIONS_SQL = """
			WITH window_counts AS (
			    SELECT w.session_id,
			           count(*) FILTER (WHERE w.data_status = 'DUE') AS due,
			           count(*) FILTER (WHERE w.data_status = 'CLAIMED') AS claimed,
			           count(*) FILTER (WHERE w.data_status = 'VALID') AS valid,
			           count(*) FILTER (WHERE w.data_status = 'VALID_EMPTY') AS valid_empty,
			           count(*) FILTER (WHERE w.data_status = 'INCOMPLETE') AS incomplete,
			           count(*) FILTER (WHERE w.data_status = 'MISSING') AS missing,
			           count(*) FILTER (WHERE w.data_status = 'INVALID') AS invalid,
			           count(*) FILTER (WHERE
			""" + NO_EVIDENCE_PREDICATE + """
			           ) AS overdue_no_evidence,
			           count(*) FILTER (WHERE w.failed_unit_count > 0) AS failed_unit_windows
			      FROM minute_ingestion_window w
			      JOIN minute_ingestion_session s ON s.session_id = w.session_id
			     WHERE s.session_date BETWEEN ? AND ?
			     GROUP BY w.session_id
			), price_counts AS (
			    SELECT j.session_id,
			""" + PRICE_JOB_COUNT_COLUMNS + """
			      FROM price_window_job j
			      JOIN minute_ingestion_session s ON s.session_id = j.session_id
			      LEFT JOIN dataset_commit_outbox o
			        ON o.event_id = 'PriceWindowCommitted:' || j.job_id || ':' || j.redrive_generation
			     WHERE s.session_date BETWEEN ? AND ?
			     GROUP BY j.session_id
			)
			SELECT s.dataset, s.source_group, s.session_date, s.phase,
			       s.expected_window_count,
			       CASE WHEN s.lease_expires_at IS NULL THEN NULL
			            ELSE s.lease_expires_at < now() END AS lease_expired,
			       COALESCE(w.due, 0) AS due, COALESCE(w.claimed, 0) AS claimed,
			       COALESCE(w.valid, 0) AS valid, COALESCE(w.valid_empty, 0) AS valid_empty,
			       COALESCE(w.incomplete, 0) AS incomplete, COALESCE(w.missing, 0) AS missing,
			       COALESCE(w.invalid, 0) AS invalid,
			       COALESCE(w.overdue_no_evidence, 0) AS overdue_no_evidence,
			       COALESCE(w.failed_unit_windows, 0) AS failed_unit_windows,
			       COALESCE(p.waiting, 0) AS waiting, COALESCE(p.claimed, 0) AS claimed_jobs,
			       COALESCE(p.claimed_expired, 0) AS claimed_expired,
			       COALESCE(p.succeeded, 0) AS succeeded, COALESCE(p.dead, 0) AS dead,
			       COALESCE(p.delivery_failed, 0) AS delivery_failed
			  FROM minute_ingestion_session s
			  LEFT JOIN window_counts w ON w.session_id = s.session_id
			  LEFT JOIN price_counts p ON p.session_id = s.session_id
			 WHERE s.session_date BETWEEN ? AND ?
			 ORDER BY s.session_date, s.dataset, s.source_group, s.session_id
			""";

	private static final String DAILY_NEWS_JOBS_SQL = """
			SELECT (j.created_at AT TIME ZONE 'Asia/Seoul')::date AS job_date,
			""" + NEWS_JOB_COUNT_COLUMNS + """
			  FROM news_extraction_job j
			  LEFT JOIN dataset_commit_outbox o
			    ON o.event_id = 'NewsExtractionRequested:' || j.job_id || ':' || j.redrive_generation
			 WHERE j.created_at >= ? AND j.created_at < ?
			 GROUP BY (j.created_at AT TIME ZONE 'Asia/Seoul')::date
			 ORDER BY job_date
			""";

	private final JdbcTemplate jdbc;

	public JdbcMinuteStatusRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public MinuteStatus status(LocalDate sessionDate) {
		Map<String, WindowCounts> counts = new HashMap<>();
		jdbc.query(WINDOW_COUNTS_SQL, rs -> {
			counts.put(rs.getString("session_id"), new WindowCounts(
					rs.getLong("due"), rs.getLong("claimed"), rs.getLong("valid"),
					rs.getLong("valid_empty"), rs.getLong("incomplete"), rs.getLong("missing"),
					rs.getLong("invalid"), rs.getLong("overdue_no_evidence")));
		}, sessionDate);

		Map<String, List<GapWindow>> gaps = new HashMap<>();
		jdbc.query(GAPS_SQL, rs -> {
			gaps.computeIfAbsent(rs.getString("session_id"), k -> new ArrayList<>())
					.add(new GapWindow(
							rs.getObject("window_start", OffsetDateTime.class),
							rs.getObject("window_end", OffsetDateTime.class),
							rs.getString("data_status"),
							rs.getBoolean("no_evidence")));
		}, sessionDate);

		Map<String, JobCounts> priceJobs = new HashMap<>();
		jdbc.query(PRICE_JOBS_SQL, rs -> {
			priceJobs.put(rs.getString("session_id"), mapJobs(rs));
		}, sessionDate);

		List<SessionSummary> sessions = jdbc.query(SESSIONS_SQL, (rs, i) -> {
			String sessionId = rs.getString("session_id");
			return new SessionSummary(
					sessionId,
					rs.getString("dataset"),
					rs.getString("source_group"),
					rs.getDate("session_date").toLocalDate(),
					rs.getString("phase"),
					rs.getString("universe_version"),
					rs.getInt("expected_window_count"),
					rs.getObject("processed_through", OffsetDateTime.class),
					rs.getObject("contiguous_complete_through", OffsetDateTime.class),
					rs.getObject("heartbeat_at", OffsetDateTime.class),
					rs.getObject("lease_expires_at", OffsetDateTime.class),
					nullableBoolean(rs, "lease_expired"),
					// 창 행 0개 = 아직 materialize 전 — 집계 0 은 그 자체로 사실이다
					counts.getOrDefault(sessionId, new WindowCounts(0, 0, 0, 0, 0, 0, 0, 0)),
					gaps.getOrDefault(sessionId, List.of()),
					priceJobs.getOrDefault(sessionId, new JobCounts(0, 0, 0, 0, 0, 0)));
		}, sessionDate);

		OffsetDateTime dayStart = sessionDate.atStartOfDay(KST).toOffsetDateTime();
		OffsetDateTime dayEnd = sessionDate.plusDays(1).atStartOfDay(KST).toOffsetDateTime();
		JobCounts newsJobs = jdbc.query(NEWS_JOBS_SQL,
				(rs, i) -> mapJobs(rs), dayStart, dayEnd).get(0);
		return new MinuteStatus(sessions, newsJobs);
	}

	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public DailyStatus dailyStatus(LocalDate fromInclusive, LocalDate toInclusive) {
		List<DailySessionSummary> sessions = jdbc.query(DAILY_SESSIONS_SQL, (rs, i) ->
				new DailySessionSummary(
						rs.getString("dataset"), rs.getString("source_group"),
						rs.getDate("session_date").toLocalDate(), rs.getString("phase"),
						rs.getInt("expected_window_count"), nullableBoolean(rs, "lease_expired"),
						new DailyWindowCounts(
								rs.getLong("due"), rs.getLong("claimed"), rs.getLong("valid"),
								rs.getLong("valid_empty"), rs.getLong("incomplete"),
								rs.getLong("missing"), rs.getLong("invalid"),
								rs.getLong("overdue_no_evidence"),
								rs.getLong("failed_unit_windows")),
						new JobCounts(rs.getLong("waiting"), rs.getLong("claimed_jobs"),
								rs.getLong("claimed_expired"), rs.getLong("succeeded"),
								rs.getLong("dead"), rs.getLong("delivery_failed"))),
				fromInclusive, toInclusive, fromInclusive, toInclusive,
				fromInclusive, toInclusive);

		OffsetDateTime rangeStart = fromInclusive.atStartOfDay(KST).toOffsetDateTime();
		OffsetDateTime rangeEnd = toInclusive.plusDays(1).atStartOfDay(KST).toOffsetDateTime();
		Map<LocalDate, JobCounts> newsJobs = new HashMap<>();
		jdbc.query(DAILY_NEWS_JOBS_SQL, rs -> {
			newsJobs.put(rs.getDate("job_date").toLocalDate(), mapJobs(rs));
		}, rangeStart, rangeEnd);
		return new DailyStatus(sessions, Map.copyOf(newsJobs));
	}

	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

	private static JobCounts mapJobs(ResultSet rs) throws SQLException {
		return new JobCounts(rs.getLong("waiting"), rs.getLong("claimed"),
				rs.getLong("claimed_expired"), rs.getLong("succeeded"), rs.getLong("dead"),
				rs.getLong("delivery_failed"));
	}

	/** lease 부재(NULL)와 만료를 뭉개지 않는다 — getBoolean 은 NULL 을 false 로 돌려준다. */
	private static Boolean nullableBoolean(ResultSet rs, String column) throws SQLException {
		boolean value = rs.getBoolean(column);
		return rs.wasNull() ? null : value;
	}
}
