package com.edge.superadmin.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.Comparator;
import java.util.List;

/**
 * {@link ConsoleFactsRepository} 의 JdbcTemplate 구현(ALPHA-738).
 *
 * <p>이 조각은 <b>조회 창</b>만 정한다 — 어느 하루를 볼지 고르고, 무엇을 봤는지 되돌려준다.
 * 사실 축(런·작업·데이터셋·산출·경계)은 뒤따르는 조각이 하나씩 더한다.
 *
 * <p>날짜 축은 <b>거래일</b>({@code trading_date})이다. 다만 비거래일 런은 그 컬럼이 NULL 이라
 * 거래일만으로 자르면 통째로 새어 나간다({@link JdbcPipelineStatusRepository} 격자 주석과 같은
 * 사실) — 그래서 NULL 인 런만 계획 시각({@code created_at})의 KST 날짜로 줍는다.
 *
 * <p>축이 붙으면 그 조회들은 한 REPEATABLE READ 스냅샷에서 돈다 — 인터페이스 주석의 이유.
 */
@Repository
public class JdbcConsoleFactsRepository implements ConsoleFactsRepository {

	/**
	 * <b>이 런은 어느 날의 것인가</b> — 거래일이 있으면 거래일, 없으면(비거래일 런) 계획 시각의
	 * KST 날짜. 축이 붙으면 이 식을 쓰는 자리가 여럿이 된다(조회 창 · 작업 조인 · 최신 날짜).
	 * 한 자리라도 다르게 쓰면 그 런이 <b>창에는 들어오는데 최신 날짜에는 안 잡혀</b> 기본 화면에서
	 * 사라진다(리뷰가 잡았다 — 최신 날짜만 {@code trading_date} 를 보고 있었다). 그래서 상수다.
	 */
	private static final String RUN_DAY =
			"COALESCE(r.trading_date, (r.created_at AT TIME ZONE 'Asia/Seoul')::date)";

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
		return new ConsoleFacts(day, meta.dbNow());
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

	/** 달력에 없는 날짜는 null — 못 읽는 키 하나가 조회를 죽이지 않는다. */
	private static LocalDate parseDate(String text) {
		try {
			return LocalDate.parse(text);
		} catch (java.time.format.DateTimeParseException e) {
			return null;
		}
	}
}
