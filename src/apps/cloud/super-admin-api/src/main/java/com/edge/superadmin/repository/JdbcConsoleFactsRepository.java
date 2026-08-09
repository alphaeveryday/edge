package com.edge.superadmin.repository;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowCallbackHandler;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Comparator;
import java.util.Map;
import java.util.TreeSet;
import java.util.stream.Collectors;

/**
 * {@link ConsoleFactsRepository} 의 JdbcTemplate 구현(ALPHA-738).
 *
 * <p>날짜 축은 <b>거래일</b>({@code trading_date})이다. 다만 비거래일 런은 그 컬럼이 NULL 이라
 * 거래일만으로 자르면 통째로 새어 나간다({@link JdbcPipelineStatusRepository} 격자 주석과 같은
 * 사실) — 그래서 NULL 인 런만 계획 시각({@code created_at})의 KST 날짜로 줍는다.
 *
 * <p>여섯 조회는 한 REPEATABLE READ 스냅샷에서 돈다 — 인터페이스 주석의 이유.
 */
@Repository
public class JdbcConsoleFactsRepository implements ConsoleFactsRepository {

	private static final Logger log =
			LoggerFactory.getLogger(JdbcConsoleFactsRepository.class);

	/**
	 * <b>이 런은 어느 날의 것인가</b> — 거래일이 있으면 거래일, 없으면(비거래일 런) 계획 시각의
	 * KST 날짜. 이 식을 쓰는 자리가 셋이다(조회 창 · 작업 조인 · 최신 날짜). 한 자리라도 다르게
	 * 쓰면 그 런이 <b>창에는 들어오는데 최신 날짜에는 안 잡혀</b> 기본 화면에서 사라진다
	 * (리뷰 5라운드가 잡았다 — 최신 날짜만 {@code trading_date} 를 보고 있었다).
	 */
	private static final String RUN_DAY =
			"COALESCE(r.trading_date, (r.created_at AT TIME ZONE 'Asia/Seoul')::date)";

	/** 런과 작업이 <b>같은 조각</b>을 써야 "작업은 있는데 그 런이 없는" 응답이 안 나온다. */
	private static final String DAY_WINDOW = "(%s = ?)".formatted(RUN_DAY);

	/*
	 * ⚠️ 날짜 후보 조회에 <b>하한(lookback)을 두지 않는다.</b> 한 번 뒀다가 되돌렸다(리뷰 6→7라운드):
	 * 90일 창은 파이프라인이 그보다 오래 멈췄다 재개한 날 <b>정당한 직전 10거래일을 잘라</b> R13 을
	 * 못 돌게 하거나 잘못 판정하게 만든다 — 안 해도 될 성능 대비로 계약을 깎은 것이었다.
	 *
	 * 규모가 그 대비를 요구하지 않는다: `ops_pipeline_run` 은 레인 4개 × 하루 1~3슬롯이라 연 수천 행,
	 * 날짜 조회는 그중 `DISTINCT` 날짜(운영 일수)만 낸다. 콘솔 단발 조회에 감당 범위이고, 같은
	 * 판단을 {@link JdbcNewsLineageRepository} 가 이미 문서화해 뒀다. 느려지면 인덱스가 먼저다.
	 */

	/**
	 * 원장이 아는 <b>가장 최근 날</b>의 런 축 후보({@code run_latest}) — <b>거래일이라는 보장은
	 * 없다</b>. {@link #latestDay} 가 여기에 계획 결손일을 합쳐 최종 날짜를 정한다. 둘 다 없으면
	 * DB 시계의 KST 오늘로 떨어진다 — 그때 런·작업이 빈 것은 사실이고, 날짜 자체가 없으면 화면이
	 * "무엇을 본 응답인가"를 말할 수 없다.
	 *
	 * <p>⚠️ {@code trading_date} 로 단순화하지 마라 — 비거래일 런({@code RUN_DAY} 참조)과 계획
	 * 결손일이 빠져 런이 0건인 날의 R01 P0 가 기본 화면에서 통째로 사라진다.
	 */
	private static final String META_SQL = """
			SELECT now() AS db_now,
			       (now() AT TIME ZONE 'Asia/Seoul')::date AS kst_today,
			       (SELECT max(%s)
			          FROM ops_pipeline_run r
			         WHERE %s <= (now() AT TIME ZONE 'Asia/Seoul')::date) AS run_latest
			""".formatted(RUN_DAY, RUN_DAY);

	private static final String RUNS_SQL = """
			SELECT r.run_key, r.pipeline_type, r.trading_date, r.orchestration_status,
			       r.updated_at, r.hard_deadline_at
			  FROM ops_pipeline_run r
			 WHERE %s
			 ORDER BY r.run_key, r.pipeline_run_id
			""".formatted(DAY_WINDOW);

	/**
	 * 런 행이 없는 계획 슬롯 — 이 응답에서 <b>런처럼 생긴 행</b>으로 나간다(R01 의 대상).
	 *
	 * <p>{@code status = 'OPEN'} 만으로는 부족해 {@code NOT EXISTS} 를 함께 건다. Reconciler 가
	 * 아직 닫지 못한 이슈와 그 사이 생긴 런이 겹치면 같은 {@code run_key} 가 두 행으로 나가고,
	 * 엔진은 식별자 충돌을 만나 <b>R01 을 통째로 못 돎</b> 으로 세운다(계약 §사건 식별자).
	 *
	 * <p>슬롯 날짜는 키에서 읽는다 — Reconciler 의 {@code evidence} 에는 {@code run_key} 뿐이고
	 * {@code ops_reconciliation_issue} 에 레인·거래일 컬럼이 없다. 키 형식의 SSOT 는
	 * data-pipeline {@code ops/planner.py::slot_run_key}({@code <lane>:<YYYY-MM-DDTHH:MM>} KST).
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
	 * stage 정렬을 CASE 로 고정하는 이유는 격자와 같다(문자열 정렬이면 파이프라인 역순이 된다).
	 *
	 * <p>{@code attempts} 는 상관 서브쿼리 한 번이다 — 시도 <b>이력</b>이 아니라 개수만 필요하고
	 * (규칙은 상한 대비 횟수를 본다), 조인하면 작업 행이 시도 수만큼 불어난다.
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
	 * 기준(중앙값)을 잴 <b>직전 거래일</b> 목록.
	 *
	 * <p>⚠️ <b>{@code trading_date} 는 거래일 달력이 아니다.</b> Planner 는 슬롯 날짜를 그대로 쓰고
	 * ({@code plan_slot} 이 {@code is_trading_day} 와 <b>무관하게</b> 채운다), 휴장 판정은 기대 작업
	 * 쪽에 {@code skip_reason = 'NON_TRADING_DAY'} 로 남는다. 안 빼면 평일 휴장일의 산출 0 이 표본에
	 * 들어가 중앙값이 내려가고 R13 이 둔해진다.
	 *
	 * <p>🔴 <b>제외는 런 단위가 아니라 날짜 단위다.</b> 휴장 신호는 KR 시장 레인에만 붙는데
	 * ({@code planner.py} 의 {@code skip = (not trading) and kr_trading_calendar}) 뉴스·공시 레인은
	 * 휴장일에도 돈다 — 런 단위로 상관시키면 그 레인의 런이 <b>같은 날짜를 다시 표본에 넣는다</b>
	 * (리뷰 4라운드가 잡았다. 3라운드가 넣은 가드가 정확히 그 모양이었다).
	 *
	 * <p>🔴 <b>여기서 10개로 자르지 않는다.</b> 자르고 나서 휴장일을 빼면 그중 하나가 휴장일일 때
	 * 표본이 9개로 줄고 11번째 거래일은 영영 안 들어온다 — {@code slotDays} 에서 고친 "자르고 나서
	 * 거른다"가 런 축에 그대로 남아 있던 자리다(리뷰 6라운드). 자르는 것은 {@link #baseDays} 가
	 * 합집합·제외를 다 끝낸 뒤에 한다.
	 */
	private static final String BASE_DAYS_SQL = """
			SELECT DISTINCT r.trading_date
			  FROM ops_pipeline_run r
			 WHERE r.trading_date IS NOT NULL AND r.trading_date <= ?
			 ORDER BY r.trading_date DESC
			""";

	/**
	 * 🔴 <b>런 행이 없는 날도 원장이 아는 날이다.</b> Planner 가 통째로 실패하면 그날은
	 * {@code ops_pipeline_run} 에 한 행도 없다. 두 자리에서 쓴다:
	 * <ul>
	 *   <li><b>최신 날짜</b>({@link #latestDay}) — 안 쓰면 기본 조회가 <b>어제</b>를 보고 그날의
	 *       R01 P0(계획 슬롯 미기동)가 화면에서 통째로 사라진다. 하필 콘솔이 가장 시끄러워야 하는 날에.</li>
	 *   <li><b>중앙값 표본</b> — 안 쓰면 그날의 실측 0 이 표본에서 빠진다. ⚠️ "빼는 쪽이 과민이라
	 *       안전하다"는 3라운드의 논거는 <b>틀렸다</b>(4라운드 반례): R13 은 ±25% <b>양방향</b>이라
	 *       기준이 올라가면 위쪽 이상이 조용해진다 — 거짓 음성도 같이 만든다. 어느 쪽도 안전하지
	 *       않으므로 <b>더 그럴듯한 쪽</b>을 고른다: {@code PLANNER_MISSING} 은 주말을 걸러 열리고
	 *       (`_due_slots` 의 `weekday() >= 5`), 휴장일에는 Planner 가 정상적으로 돌아 런 행이 생기므로
	 *       (그래서 휴장 신호도 그때 남는다) <b>런이 0건인 평일 = 거래일</b> 일 확률이 압도적이다.</li>
	 * </ul>
	 * 남는 불확실은 "장애와 휴장이 겹친 날" 하나이고, 아래 「남은 계측 부채」에 적혀 있다.
	 *
	 * <p>OPEN 만 본다 — Reconciler 는 그 슬롯의 런이 생기면 이슈를 닫으므로 RESOLVED 는 런 행이
	 * 있다는 뜻이고, 그날은 런 조회가 이미 덮는다. 인덱스({@code ix_ops_issue_status_type})도 탄다.
	 * ⚠️ 이 술어는 <b>순수 최적화</b>라 테스트로 겨눌 수 없다(빼도 결과가 같다 — 변이 검증에서
	 * 확인). 사건 행을 만드는 {@code MISSING_SLOTS_SQL} 쪽 OPEN 필터는 다르다: 그건 유령 런을
	 * 막는 가드이고 단언이 걸려 있다.
	 *
	 * <p>날짜는 <b>텍스트로</b> 꺼내 자바에서 파싱한다. SQL 에서 {@code ::date} 로 캐스트하면 형식만
	 * 맞고 달력에 없는 날(`2026-02-31`)이 든 키 하나가 <b>조회 전체를 죽인다</b> — 콘솔이 통째로
	 * 안 뜨는 것보다 그 슬롯 하나를 못 읽는 쪽이 낫다. ISO 날짜는 사전순 = 시간순이라 텍스트
	 * 비교로 창을 자를 수 있다.
	 */
	/**
	 * 휴장일 — 그날 어느 런이든 KR 시장 작업을 {@code NON_TRADING_DAY} 로 건너뛰었으면 휴장이다.
	 *
	 * <p>🔴 <b>제외는 합집합을 만든 뒤 한 번만 한다.</b> 런 축 조회 안에서 빼면 계획 결손일 합집합이
	 * 같은 날짜를 <b>다시 넣는다</b> — 휴장일에도 뉴스 레인은 돌고 `_due_slots` 는 레인별로 슬롯을
	 * 만들어서, 시장 레인이 휴장을 기록한 날에 뉴스 레인의 결손 슬롯이 남을 수 있다(리뷰 5라운드).
	 * 가드가 둘이면 한쪽만 고쳐진다는 이 트랙의 규칙 그대로다.
	 */
	private static final String HOLIDAY_DAYS_SQL = """
			SELECT DISTINCT r.trading_date
			  FROM ops_pipeline_run r
			  JOIN ops_expected_task t ON t.pipeline_run_id = r.pipeline_run_id
			 WHERE r.trading_date IS NOT NULL AND r.trading_date <= ?
			   AND t.skip_reason = 'NON_TRADING_DAY'
			""";

	private static final String MISSING_SLOT_DAYS_SQL = """
			SELECT DISTINCT substring(i.scope_key from ':(\\d{4}-\\d{2}-\\d{2})T') AS slot_date
			  FROM ops_reconciliation_issue i
			 WHERE i.status = 'OPEN'
			   AND i.issue_type = 'PLANNER_MISSING'
			   AND i.scope = 'slot'
			   AND substring(i.scope_key from ':(\\d{4}-\\d{2}-\\d{2})T') <= ?
			 ORDER BY 1 DESC
			""";

	/**
	 * 산출 축 — id·라벨·단위는 프론트 어휘({@code outputs[].id})와 1:1 이고 SQL 은 날짜별 카운트를
	 * 낸다. {@code %s} 자리에 날짜 플레이스홀더가 들어간다.
	 *
	 * <p>날짜 축이 둘이다: 거래일 컬럼을 가진 산출({@code trade_date})과 수집 시각뿐인 산출
	 * ({@code available_at} 의 KST 날짜 — 뉴스 계보 조회와 같은 규칙).
	 *
	 * <p>⚠️ 뒤 세 산출은 <b>표현식 필터라 인덱스를 못 탄다</b>({@link JdbcNewsLineageRepository} 가
	 * 같은 이유로 이미 감수한 비용). 콘솔 단발 조회 규모라 지금은 감당 범위지만, 여기는 그쪽과 달리
	 * 누적 테이블 <b>셋</b>을 한 요청에서 훑는다 — 느려지면 함수 인덱스가 먼저 붙을 자리다.
	 */
	private record OutputSpec(String id, String label, String unit, String sql) {
	}

	private static final List<OutputSpec> OUTPUTS = List.of(
			new OutputSpec("o.pub", "게시 ETF", "종", """
					SELECT trade_date AS d, count(DISTINCT etf_instrument_id) AS n
					  FROM explanation_result
					 WHERE publication_status = 'PUBLISHED' AND trade_date IN (%s)
					 GROUP BY trade_date"""),
			new OutputSpec("o.trig", "배치 트리거", "종", """
					SELECT trade_date AS d, count(DISTINCT etf_instrument_id) AS n
					  FROM price_movement_trigger
					 WHERE trade_date IN (%s)
					 GROUP BY trade_date"""),
			new OutputSpec("o.doc", "뉴스 문서", "건", """
					SELECT (available_at AT TIME ZONE 'Asia/Seoul')::date AS d, count(*) AS n
					  FROM document
					 WHERE document_type = 'NEWS'
					   AND (available_at AT TIME ZONE 'Asia/Seoul')::date IN (%s)
					 GROUP BY 1"""),
			new OutputSpec("o.asr", "assertion", "건", """
					SELECT (available_at AT TIME ZONE 'Asia/Seoul')::date AS d, count(*) AS n
					  FROM document_assertion
					 WHERE (available_at AT TIME ZONE 'Asia/Seoul')::date IN (%s)
					 GROUP BY 1"""),
			new OutputSpec("o.evt", "source event", "건", """
					SELECT (available_at AT TIME ZONE 'Asia/Seoul')::date AS d, count(*) AS n
					  FROM source_event
					 WHERE (available_at AT TIME ZONE 'Asia/Seoul')::date IN (%s)
					 GROUP BY 1"""));

	/**
	 * 경계 정합 — 세 카운트를 한 문장으로 낸다(뉴스 계보 요약과 같은 이유: 쪼개면 조회 사이에
	 * writer 가 끼어 존재한 적 없는 조합이 조립된다).
	 *
	 * <p><b>{@code delivery_type} 을 따로 거르지 않는다.</b> 발번은 2형상이고(NEW·INVALIDATION —
	 * ADR-0044 로 CORRECTION 폐지), 스키마의 {@code ck_tenant_delivery_payload} 가
	 * {@code explanation_result_id} 를 <b>NEW 에만</b> 허용한다 — INVALIDATION 은 그 자리가 NULL 이라
	 * 아래 두 조건에 애초에 안 걸린다. 유형 술어를 덧붙이면 어떤 행도 못 거르는 가드가 하나 늘고,
	 * 그건 테스트로 겨눌 수도 없다(같은 이유로 마이그레이션이 자기참조 금지 제약을 지웠다).
	 * 3형상이 돌아오면 이 두 조건에 유형 술어가 <b>같이</b> 필요하다.
	 */
	private static final String BOUNDARY_SQL = """
			SELECT (SELECT count(*) FROM explanation_result r
			         WHERE r.publication_status = 'PUBLISHED'
			           AND NOT EXISTS (SELECT 1 FROM tenant_delivery d
			                            WHERE d.explanation_result_id = r.explanation_result_id))
			         AS published_without_delivery,
			       (SELECT count(*) FROM tenant_delivery d
			          JOIN explanation_result r
			            ON r.explanation_result_id = d.explanation_result_id
			         WHERE r.publication_status <> 'PUBLISHED') AS delivery_now_nonpublished,
			       (SELECT count(*) FROM tenant_delivery) AS delivery_rows
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

		List<RunRow> runs = new ArrayList<>(
				jdbc.query(RUNS_SQL, JdbcConsoleFactsRepository::mapRun, day));
		jdbc.queryForList(MISSING_SLOTS_SQL, String.class, day.toString())
				.forEach(runKey -> runs.add(missingSlot(runKey)));
		runs.sort((a, b) -> a.runKey().compareTo(b.runKey()));

		List<TaskRow> tasks = jdbc.query(TASKS_SQL, JdbcConsoleFactsRepository::mapTask, day);
		return new ConsoleFacts(day, meta.dbNow(), List.copyOf(runs), tasks, outputs(day),
				jdbc.queryForObject(BOUNDARY_SQL, (rs, i) -> new BoundaryRow(
						rs.getLong("published_without_delivery"),
						rs.getLong("delivery_now_nonpublished"),
						rs.getLong("delivery_rows"))));
	}

	private record Meta(OffsetDateTime dbNow, LocalDate kstToday, LocalDate runLatest) {
	}

	/**
	 * 날짜를 생략했을 때 볼 날 — 런이 있던 마지막 날과 <b>계획만 있던 마지막 날</b> 중 뒤쪽이다.
	 * 둘 다 없으면(원장이 비었으면) DB 시계의 KST 오늘 — 그때 런·작업이 빈 것은 사실이고, 날짜가
	 * 없으면 화면이 "무엇을 본 응답인가"를 말할 수 없다.
	 *
	 * <p>⚠️ 여기서는 <b>휴장일을 안 뺀다</b>(중앙값 표본과 다르다). 두 물음이 다르기 때문이다 —
	 * 이쪽은 "무엇을 보여줄까"라 휴장일에도 도는 뉴스·공시 런을 감춰선 안 되고, 표본은 "무엇이
	 * 평소인가"라 휴장일이 들어가면 기준이 내려간다.
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
	 * 달력에 없는 날이 사전순으로 앞서면, 잘라 온 10개가 전부 버려지고 <b>그 아래의 유효한 결손일이
	 * 사라진다</b>(리뷰 5라운드). 자르는 것은 파싱 뒤 호출부가 한다. 서로 다른 슬롯 <b>날짜</b> 수는
	 * 운영 일수로 묶여 있어 전건을 읽어도 작다.
	 */
	private List<LocalDate> slotDays(LocalDate upTo) {
		return jdbc.queryForList(MISSING_SLOT_DAYS_SQL, String.class, upTo.toString()).stream()
				.map(JdbcConsoleFactsRepository::parseDate)
				.filter(java.util.Objects::nonNull)
				.sorted(Comparator.reverseOrder())
				.toList();
	}

	/**
	 * 기준(중앙값)을 잴 직전 거래일 — 런이 있던 날(휴장일 제외)과 계획 결손일의 합집합에서 최근 10개.
	 * <b>합친 뒤에</b> 자른다: 한쪽만 10개로 자르고 합치면 더 최근인 다른 쪽 날이 밀려난다.
	 */
	private List<LocalDate> baseDays(LocalDate day) {
		LocalDate upTo = day.minusDays(1);
		TreeSet<LocalDate> days = new TreeSet<>(Comparator.reverseOrder());
		days.addAll(jdbc.queryForList(BASE_DAYS_SQL, LocalDate.class, upTo));
		days.addAll(slotDays(upTo));
		// 휴장일 제외는 **합집합 뒤 한 번**, 자르는 것은 **그 뒤** — 각 SQL 주석의 이유.
		days.removeAll(jdbc.queryForList(HOLIDAY_DAYS_SQL, LocalDate.class, upTo));
		return days.stream().limit(10).toList();
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

	/**
	 * 산출별 오늘 값과 직전 10거래일 중앙값. 산출마다 조회 한 번이고 오늘·기준일을 <b>한 문장</b>
	 * 으로 센다 — 나눠 세면 두 값이 다른 스냅샷에서 나와 편차율이 존재한 적 없는 비교가 된다.
	 */
	private List<OutputRow> outputs(LocalDate day) {
		List<LocalDate> baseDays = baseDays(day);
		List<LocalDate> queried = new ArrayList<>(baseDays);
		queried.add(day);   // 기준일 목록은 day 미만이라 오늘과 겹치지 않는다
		String placeholders = queried.stream().map(d -> "?").collect(Collectors.joining(","));

		return OUTPUTS.stream().map(spec -> {
			Map<LocalDate, Long> byDay = new HashMap<>();
			// 캐스트가 없으면 varargs 오버로드가 ResultSetExtractor 와 겹쳐 모호해진다.
			jdbc.query(spec.sql().formatted(placeholders),
					(RowCallbackHandler) rs -> byDay.put(rs.getObject("d", LocalDate.class),
							rs.getLong("n")),
					queried.toArray());
			// 결과에 없는 거래일은 0 이다 — 그날 산출이 없었다는 실측이지 모름이 아니다.
			List<Long> base = baseDays.stream().map(d -> byDay.getOrDefault(d, 0L)).toList();
			return new OutputRow(spec.id(), spec.label(), spec.unit(),
					byDay.getOrDefault(day, 0L), median(base));
		}).toList();
	}

	/** 달력에 없는 날짜는 null — 슬롯 키 파싱과 같은 규약이다(못 읽는 하나가 조회를 죽이지 않는다). */
	private static LocalDate parseDate(String text) {
		try {
			return LocalDate.parse(text);
		} catch (java.time.format.DateTimeParseException e) {
			return null;
		}
	}

	/** 표본이 없으면 null — 기준 없음이지 0 이 아니다. 짝수면 가운데 둘의 평균이다. */
	private static Double median(List<Long> values) {
		if (values.isEmpty()) {
			return null;
		}
		List<Long> sorted = values.stream().sorted().toList();
		int n = sorted.size();
		return n % 2 == 1
				? (double) sorted.get(n / 2)
				: (sorted.get(n / 2 - 1) + sorted.get(n / 2)) / 2.0;
	}

	private static Long nullableLong(ResultSet rs, String column) throws SQLException {
		long value = rs.getLong(column);
		return rs.wasNull() ? null : value;
	}
}
