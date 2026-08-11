package com.edge.superadmin.repository;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * {@link ConsoleFactsRepository} 의 JdbcTemplate 구현(ALPHA-738).
 *
 * <p>지금 내는 것은 <b>조회 창 + 런 축(계획 결손 슬롯 포함) + 작업 축</b>이다. 산출·경계는
 * 뒤따르는 조각이 하나씩 더한다. 와이어의 데이터셋 축은 여기서 안 낸다 — 작업의 계약·신선도
 * 컬럼을 재료로 {@code ConsoleFactsService} 가 접는다.
 *
 * <p>날짜 축은 <b>거래일</b>({@code trading_date})이다. 다만 비거래일 런은 그 컬럼이 NULL 이라
 * 거래일만으로 자르면 통째로 새어 나간다({@link JdbcPipelineStatusRepository} 격자 주석과 같은
 * 사실) — 그래서 NULL 인 런만 계획 시각({@code created_at})의 KST 날짜로 줍는다.
 *
 * <p>축이 붙으면 그 조회들은 한 REPEATABLE READ 스냅샷에서 돈다 — 인터페이스 주석의 이유.
 */
@Repository
public class JdbcConsoleFactsRepository implements ConsoleFactsRepository {

	private static final Logger log = LoggerFactory.getLogger(JdbcConsoleFactsRepository.class);

	/**
	 * <b>이 런은 어느 날의 것인가</b> — 거래일이 있으면 거래일, 없으면(비거래일 런) 계획 시각의
	 * KST 날짜. 축이 붙으면 이 식을 쓰는 자리가 여럿이 된다(조회 창 · 작업 조인 · 최신 날짜).
	 * 한 자리라도 다르게 쓰면 그 런이 <b>창에는 들어오는데 최신 날짜에는 안 잡혀</b> 기본 화면에서
	 * 사라진다(리뷰가 잡았다 — 최신 날짜만 {@code trading_date} 를 보고 있었다). 그래서 상수다.
	 */
	private static final String RUN_DAY =
			"COALESCE(r.trading_date, (r.created_at AT TIME ZONE 'Asia/Seoul')::date)";

	/** 런과 작업이 <b>같은 조각</b>을 써야 "작업은 있는데 그 런이 없는" 응답이 안 나온다. */
	private static final String DAY_WINDOW = "(%s = ?)".formatted(RUN_DAY);

	/**
	 * 그 날의 런 전건. <b>정렬을 고정한다</b> — 안 하면 같은 원장이 조회마다 다른 순서로 나가고,
	 * 소비자가 "첫 런"을 집는 순간 판정이 흔들린다.
	 *
	 * <p>⚠️ <b>여기서 정렬하지 않는다.</b> 계획 결손 슬롯을 합친 뒤 {@link #facts} 가 전부 다시
	 * 정렬하므로 이 자리의 {@code ORDER BY} 는 아무것도 정하지 않는다. 그런데 <b>가만두면 해롭다</b>:
	 * 자바 정렬은 안정 정렬이라 SQL 이 미리 {@code run_key} 순으로 줘 버리면 자바 쪽 정렬 키를
	 * 무엇으로 바꾸든 결과가 같아진다 — 두 정렬이 <b>서로를 가려</b> 어느 쪽도 안 걸린다.
	 * 순서의 계약은 {@code facts()} 한 곳에 둔다.
	 */
	private static final String RUNS_SQL = """
			SELECT r.run_key, r.pipeline_type, r.trading_date, r.orchestration_status,
			       r.updated_at, r.hard_deadline_at
			  FROM ops_pipeline_run r
			 WHERE %s
			""".formatted(DAY_WINDOW);

	/*
	 * ⚠️ 날짜 후보 조회에 <b>하한(lookback)을 두지 않는다.</b> 한 번 뒀다가 되돌렸다: 90일 창은
	 * 파이프라인이 그보다 오래 멈췄다 재개한 날 <b>원장이 아는 마지막 날을 창 밖으로 밀어낸다</b> —
	 * `run_latest` 도 계획 결손일도 안 잡혀 기본 조회가 KST 오늘로 떨어지고, 화면은 사고 난 그날
	 * 대신 빈 오늘을 연다. 안 해도 될 성능 대비로 계약을 깎은 것이었다.
	 *
	 * 규모가 그 대비를 요구하지 않는다: `ops_pipeline_run` 은 레인 4개 × 하루 1~3슬롯이라 연 수천 행,
	 * 날짜 조회는 그중 `DISTINCT` 날짜(운영 일수)만 낸다. 콘솔 단발 조회에 감당 범위이고, 같은
	 * 판단을 {@link JdbcNewsLineageRepository} 가 이미 문서화해 뒀다. 느려지면 인덱스가 먼저다.
	 */

	/**
	 * 원장이 아는 <b>가장 최근 날</b>의 런 축 후보({@code run_latest}) — <b>거래일이라는 보장은
	 * 없다</b>. {@link #latestDay} 가 여기에 계획 결손일을 합쳐 최종 날짜를 정한다. 둘 다 없으면
	 * DB 시계의 KST 오늘로 떨어진다 — 그때 사실이 빈 것은 사실이고, 날짜 자체가 없으면 화면이
	 * "무엇을 본 응답인가"를 말할 수 없다.
	 *
	 * <p>⚠️ {@code trading_date} 로 단순화하지 마라 — 비거래일 런({@code RUN_DAY} 참조)과 계획
	 * 결손일이 빠져, 런이 0건인 날의 사실이 기본 화면에서 통째로 사라진다.
	 */
	private static final String META_SQL = """
			SELECT now() AS db_now,
			       (now() AT TIME ZONE 'Asia/Seoul')::date AS kst_today,
			       (SELECT max(%s)
			          FROM ops_pipeline_run r
			         WHERE %s <= (now() AT TIME ZONE 'Asia/Seoul')::date) AS run_latest
			""".formatted(RUN_DAY, RUN_DAY);

	/**
	 * 런 행이 없는 계획 슬롯 — 이 응답에서 <b>런처럼 생긴 행</b>으로 나간다.
	 *
	 * <p>{@code status = 'OPEN'} 만으로는 부족해 {@code NOT EXISTS} 를 함께 건다. Reconciler 가
	 * 아직 닫지 못한 이슈와 그 사이 생긴 런이 겹치면 같은 {@code run_key} 가 <b>두 행</b>으로
	 * 나가고, 소비자는 그걸 식별자 충돌로 읽어 그 축 규칙을 통째로 못 돎 으로 세운다.
	 *
	 * <p>슬롯 날짜는 키에서 읽는다 — Reconciler 의 {@code evidence} 에는 {@code run_key} 뿐이고
	 * {@code ops_reconciliation_issue} 에 레인·거래일 컬럼이 없다. 키 형식의 SSOT 는
	 * data-pipeline {@code ops/planner.py} 의 {@code slot_run_key}
	 * ({@code <lane>:<YYYY-MM-DDTHH:MM>} KST).
	 */
	private static final String MISSING_SLOTS_SQL = """
			SELECT i.scope_key
			  FROM ops_reconciliation_issue i
			 WHERE i.issue_type = 'PLANNER_MISSING'
			   AND i.scope = 'slot'
			   AND i.status = 'OPEN'
			   AND i.scope_key LIKE '%:' || ? || 'T%'
			   AND NOT EXISTS (SELECT 1 FROM ops_pipeline_run r WHERE r.run_key = i.scope_key)
			 ORDER BY i.scope_key
			""";

	/**
	 * stage 정렬을 CASE 로 고정하는 이유는 격자와 같다 — 문자열 정렬이면 파이프라인 역순이 된다
	 * ({@code feature} < {@code normalize} < {@code raw}).
	 *
	 * <p>{@code attempts} 는 상관 서브쿼리 한 번이다 — 시도 <b>이력</b>이 아니라 개수만 필요하고
	 * (소비자는 상한 대비 횟수를 본다), 조인하면 작업 행이 시도 수만큼 불어난다.
	 */
	private static final String TASKS_SQL = """
			SELECT t.task_key, r.run_key, r.pipeline_type, r.trading_date, t.stage, t.dataset,
			       t.required, t.plan_status, t.task_outcome, t.data_status,
			       t.records_out, t.failed_records,
			       (t.completeness ->> 'expected')::bigint AS completeness_expected,
			       (t.completeness ->> 'received')::bigint AS completeness_received,
			       (t.completeness ->> 'missing')::bigint AS completeness_missing,
			       (SELECT count(*) FROM ops_task_attempt a
			         WHERE a.expected_task_id = t.expected_task_id) AS attempts,
			       t.dataset_contract_key, t.expected_as_of_date, t.actual_as_of_date,
			       t.collected_at, t.freshness_status, t.freshness_reason
			  FROM ops_expected_task t
			  JOIN ops_pipeline_run r ON r.pipeline_run_id = t.pipeline_run_id
			 WHERE %s
			 ORDER BY r.run_key,
			          CASE t.stage WHEN 'raw' THEN 0 WHEN 'normalize' THEN 1 ELSE 2 END, t.task_key
			""".formatted(DAY_WINDOW);

	/**
	 * 계획만 있고 런 행이 없는 슬롯의 <b>날짜</b> — 런이 하나도 안 뜬 날을 조회 창 후보로 살린다.
	 * 그런 날이야말로 콘솔이 열려야 하는 날이라, 런 축만 보면 기본 조회가 그 날을 건너뛴다.
	 *
	 * <p>키 형식의 SSOT 는 data-pipeline {@code ops/planner.py::slot_run_key}
	 * ({@code <lane>:<YYYY-MM-DDTHH:MM>} KST).
	 */
	private static final String MISSING_SLOT_DAYS_SQL = """
			SELECT DISTINCT substring(i.scope_key from ':(\\d{4}-\\d{2}-\\d{2})T') AS slot_date
			  FROM ops_reconciliation_issue i
			 WHERE i.status = 'OPEN'
			   AND i.issue_type = 'PLANNER_MISSING'
			   AND i.scope = 'slot'
			   AND substring(i.scope_key from ':(\\d{4}-\\d{2}-\\d{2})T') <= ?
			 ORDER BY 1 DESC
			""";

	private final JdbcTemplate jdbc;

	public JdbcConsoleFactsRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public ConsoleFacts facts(LocalDate date) {
		Meta meta = jdbc.queryForObject(META_SQL, (rs, i) -> new Meta(
				rs.getObject("db_now", OffsetDateTime.class),
				rs.getObject("kst_today", LocalDate.class),
				rs.getObject("run_latest", LocalDate.class)));
		LocalDate day = date != null ? date : latestDay(meta);

		/* 두 소스가 한 축으로 합쳐진다 — 실재하는 런과, 런 행이 없는 계획 슬롯. 합친 뒤 다시
		 * 정렬하는 이유: 각 소스가 자기 안에서만 정렬돼 있어 그냥 이어 붙이면 전체 순서가 깨진다. */
		List<RunRow> runs = new ArrayList<>(
				jdbc.query(RUNS_SQL, JdbcConsoleFactsRepository::mapRun, day));
		jdbc.queryForList(MISSING_SLOTS_SQL, String.class, day.toString())
				.forEach(runKey -> runs.add(missingSlot(runKey)));
		/* 순서의 계약은 여기 하나다 — SQL 쪽에도 두면 둘이 서로를 가린다(RUNS_SQL 주석).
		 * `run_key` 는 UNIQUE 라(`uq_ops_pipeline_run_key`) 이 키 하나로 전순서가 정해진다. */
		runs.sort(Comparator.comparing(RunRow::runKey));

		return new ConsoleFacts(day, meta.dbNow(), List.copyOf(runs),
				jdbc.query(TASKS_SQL, JdbcConsoleFactsRepository::mapTask, day));
	}

	private record Meta(OffsetDateTime dbNow, LocalDate kstToday, LocalDate runLatest) {
	}

	/**
	 * 날짜를 생략했을 때 볼 날 — 런이 있던 마지막 날과 <b>계획만 있던 마지막 날</b> 중 뒤쪽이다.
	 * 둘 다 없으면(원장이 비었으면) DB 시계의 KST 오늘 — 그때 사실이 빈 것은 사실이고, 날짜가
	 * 없으면 화면이 "무엇을 본 응답인가"를 말할 수 없다.
	 *
	 * <p>⚠️ 여기서는 <b>휴장일을 안 뺀다</b>. 물음이 "무엇을 보여줄까"라, 휴장일에도 도는 뉴스·공시
	 * 런을 감춰선 안 된다("무엇이 평소인가"를 묻는 기준선 표본과 다른 물음이다 — 그 축이 붙을 때
	 * 휴장일 제외가 그쪽에 따로 들어온다).
	 */
	private LocalDate latestDay(Meta meta) {
		LocalDate latest = meta.runLatest();
		LocalDate slotDay = latestSlotDay(meta.kstToday());
		if (slotDay != null && (latest == null || slotDay.isAfter(latest))) {
			latest = slotDay;
		}
		return latest != null ? latest : meta.kstToday();
	}

	/**
	 * 상한 이하 계획 결손 슬롯 중 가장 최근 날. 목록이 내림차순이라 처음 읽히는 것이 최댓값이다.
	 * 상한을 KST 오늘로 두는 이유: 미래 슬롯 키가 하나라도 있으면 기본 조회가 오지 않은 날로 뛴다.
	 */
	private LocalDate latestSlotDay(LocalDate upTo) {
		return slotDays(upTo).stream().findFirst().orElse(null);
	}

	/**
	 * 파싱되는 것만, 내림차순. 못 읽는 키 하나가 조회를 죽이지 않는다.
	 *
	 * <p>⚠️ SQL 에서 {@code LIMIT} 을 걸지 않는다 — 정규식은 형식만 보므로 {@code 2026-07-99} 처럼
	 * 달력에 없는 날이 사전순으로 앞서면, 잘라 온 것이 전부 버려지고 <b>그 아래의 유효한 결손일이
	 * 사라진다</b>. 자르는 것은 파싱 뒤 호출부가 한다. 서로 다른 슬롯 <b>날짜</b> 수는 운영 일수로
	 * 묶여 있어 전건을 읽어도 작다.
	 */
	private List<LocalDate> slotDays(LocalDate upTo) {
		return jdbc.queryForList(MISSING_SLOT_DAYS_SQL, String.class, upTo.toString()).stream()
				.map(JdbcConsoleFactsRepository::parseDate)
				.filter(java.util.Objects::nonNull)
				.sorted(Comparator.reverseOrder())
				.toList();
	}

	/**
	 * 원장 컬럼을 <b>그대로</b> 옮긴다 — 여기서 어휘를 다시 정의하지 않는다(판정은 클라이언트).
	 * {@code lane} 은 {@code pipeline_type} 이고, 시각 둘은 타임존을 가진 채로 나간다.
	 */
	private static RunRow mapRun(ResultSet rs, int rowNum) throws SQLException {
		return new RunRow(
				rs.getString("run_key"),
				rs.getString("pipeline_type"),
				rs.getObject("trading_date", LocalDate.class),
				rs.getString("orchestration_status"),
				rs.getObject("updated_at", OffsetDateTime.class),
				rs.getObject("hard_deadline_at", OffsetDateTime.class),
				// 실재하는 런에는 "계획된 슬롯인가"를 답할 계측이 없다 — 인터페이스 주석 참조.
				null, null);
	}

	/**
	 * 런 행이 없는 슬롯을 런 축으로 옮긴다. 키를 못 읽으면 레인·거래일을 <b>null 로 둔다</b> —
	 * 형식이 바뀌었을 때 잘못 자른 조각을 레인 이름이라고 우기는 쪽이 더 나쁘다.
	 *
	 * <p>다만 <b>조용히 넘기지는 않는다</b>(Rule 12). 여기서 못 읽히는 키는 곧 Planner 의 슬롯 키
	 * 형식이 이 조회와 갈렸다는 뜻인데, 응답에는 "레인 미상"으로만 보여 원인이 안 남는다.
	 */
	private static RunRow missingSlot(String runKey) {
		int sep = runKey.indexOf(':');
		String lane = sep > 0 ? runKey.substring(0, sep) : null;
		LocalDate slotDate = sep > 0 && runKey.length() >= sep + 11
				? parseDate(runKey.substring(sep + 1, sep + 11))
				: null;
		if (lane == null || slotDate == null) {
			log.warn("PLANNER_MISSING 슬롯 키를 못 읽었다: {} — planner 의 slot_run_key"
					+ "(<lane>:<YYYY-MM-DDTHH:MM>) 형식과 갈렸는지 확인 필요", runKey);
		}
		return new RunRow(runKey, lane, slotDate, null, null, null, true, true);
	}

	private static TaskRow mapTask(ResultSet rs, int rowNum) throws SQLException {
		return new TaskRow(
				rs.getString("task_key"),
				rs.getString("run_key"),
				rs.getString("pipeline_type"),
				rs.getObject("trading_date", LocalDate.class),
				rs.getString("stage"),
				rs.getString("dataset"),
				rs.getBoolean("required"),
				rs.getString("plan_status"),
				rs.getString("task_outcome"),
				rs.getString("data_status"),
				// getLong 은 SQL NULL 을 0 으로 준다 — "0건 처리"와 "신호 없음"이 갈려야 한다.
				nullableLong(rs, "records_out"),
				nullableLong(rs, "failed_records"),
				nullableLong(rs, "completeness_expected"),
				nullableLong(rs, "completeness_received"),
				nullableLong(rs, "completeness_missing"),
				rs.getLong("attempts"),   // count(*) 라 NULL 이 없다
				rs.getString("dataset_contract_key"),
				rs.getObject("expected_as_of_date", LocalDate.class),
				rs.getObject("actual_as_of_date", LocalDate.class),
				rs.getObject("collected_at", OffsetDateTime.class),
				rs.getString("freshness_status"),
				rs.getString("freshness_reason"));
	}

	private static Long nullableLong(ResultSet rs, String column) throws SQLException {
		long value = rs.getLong(column);
		return rs.wasNull() ? null : value;
	}

	/** 달력에 없는 날짜는 null — 못 읽는 키 하나가 조회를 죽이지 않는다. */
	private static LocalDate parseDate(String text) {
		try {
			return LocalDate.parse(text);
		} catch (java.time.format.DateTimeParseException e) {
			return null;
		}
	}
}
