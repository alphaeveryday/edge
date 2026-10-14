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
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.stream.Collectors;

/**
 * {@link ConsoleFactsRepository} 의 JdbcTemplate 구현(ALPHA-738).
 *
 * <p>원장에서 나오는 축은 여기서 전부 낸다 — <b>조회 창 + 런 축(계획 결손 슬롯 포함) + 작업 축 +
 * 산출 축 + 경계 축 + 체인 축</b>. 와이어의 데이터셋 축은 여기서 안 낸다 — 작업의 계약·신선도
 * 컬럼을 재료로 {@code ConsoleFactsService} 가 접는다.
 *
 * <p><b>원장 축</b>(런·작업·산출)의 날짜는 {@link #RUN_DAY} <b>한 식으로만</b> 묻는다 — 한 자리라도
 * 다르게 쓰면 그 런이 창에는 들어오는데 최신 날짜에 안 잡혀 기본 화면에서 사라진다.
 *
 * <p>⚠️ <b>체인 축은 그 식을 안 쓴다</b>(ALPHA-979). 세는 대상이 런이 아니라 <b>트리거</b>이고
 * 그 표들은 원장이 아니다 — {@code price_movement_trigger.trade_date} 와
 * {@code minute_price_trigger.window_start} 를 각각 본다. 즉 이 파일에는 날짜 술어가 <b>셋</b>
 * 이고, 하나로 합칠 수 없다(다른 사건의 날짜다). {@code RUN_DAY} 의 불변식은 원장 축 안에서만
 * 성립한다고 읽어라.
 *
 * <p>⚠️ <b>"비거래일 런은 그 컬럼이 NULL 이다"는 사실이 아니다.</b> {@code ops/planner.py} 의
 * {@code plan_run} 은 {@code trading_date=day.isoformat()} 을 <b>무조건</b> 넘기고, 그것이
 * {@code ops_pipeline_run} 을 만드는 유일한 프로덕션 경로다. dev 실측(2026-08-11)으로도 103행 중
 * NULL 이 <b>0건</b>이었다(저장소만으로는 재현 안 된다 — 다시 잴 것). 컬럼 자체는 nullable 이라
 * {@code RUN_DAY} 의 {@code COALESCE} 는 <b>방어</b>로 남기지만, 그걸 근거로 "거래일만 보면 샌다"고
 * 적으면 안 된다. 같은 거짓이 {@link JdbcPipelineStatusRepository} 에도 있다(이 PR 범위 밖).
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
			       r.updated_at, r.hard_deadline_at,
			       COALESCE(r.sfn_execution_arn, r.expected_execution_arn) AS execution_arn
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
			       t.records_out,
			       CASE WHEN t.task_key = 'LOAD_ETF_HOLDINGS' THEN t.unsupported_records END AS unsupported_records,
			       t.failed_records,
			       (t.completeness ->> 'expected')::bigint AS completeness_expected,
			       (t.completeness ->> 'received')::bigint AS completeness_received,
			       (t.completeness ->> 'missing')::bigint AS completeness_missing,
			       -- 업무를 실행한 시도만 센다 — DUPLICATE_SKIP 은 이미 성공한 작업의 재시도 컨테이너가
			       -- 업무 없이 끝난 흔적이라(ALPHA-1088) 세면 재시도 소진(R16)이 부풀려진다.
			       (SELECT count(*) FROM ops_task_attempt a
			         WHERE a.expected_task_id = t.expected_task_id
			           AND a.record_source <> 'DUPLICATE_SKIP') AS attempts,
			       t.dataset_contract_key, t.expected_as_of_date, t.actual_as_of_date,
			       t.collected_at, t.freshness_status, t.freshness_reason
			  FROM ops_expected_task t
			  JOIN ops_pipeline_run r ON r.pipeline_run_id = t.pipeline_run_id
			 WHERE %s
			 ORDER BY r.run_key,
			          CASE t.stage WHEN 'raw' THEN 0 WHEN 'normalize' THEN 1 ELSE 2 END, t.task_key
			""".formatted(DAY_WINDOW);

	/**
	 * 엔티티 해소율의 최근 일별 관측. {@code LOAD_ASSERTIONS} 는 feature corpus 전량을 다시 읽으므로
	 * 한 attempt의 pair가 그 실행 시점의 스냅샷이고, 같은 날 여러 런이면 가장 늦게 생성된 성공 런,
	 * 한 런의 재시도면 가장 늦게 시작한 성공 wrapper attempt를 그 날의 점으로 쓴다. 카운터가 attempt
	 * 자체에 있어 Reconciler의 task outcome 갱신이나 겹친 재시도 완료 순서와 섞이지 않는다.
	 *
	 * <p>실패·계측 없음은 점이 아니다. 0으로 합성하면 배선 전 날짜가 0% 품질로 보이므로
	 * {@code SUCCEEDED}와 비NULL pair를 함께 요구한다. pair의 범위·동시성은 스키마 CHECK가
	 * 보장하므로 읽기 경로에서 같은 검증을 복제하지 않는다.
	 */
	private static final String ENTITY_RESOLUTION_TREND_SQL = """
			WITH ranked AS (
			    SELECT %s AS d,
			           a.entity_resolution_arguments_total AS total_arguments,
			           a.entity_resolution_arguments_resolved AS resolved_arguments,
			           row_number() OVER (
			               PARTITION BY %s
			               ORDER BY r.created_at DESC, a.started_at DESC, a.attempt_id DESC
			           ) AS rn
			      FROM ops_expected_task t
			      JOIN ops_pipeline_run r ON r.pipeline_run_id = t.pipeline_run_id
			      JOIN ops_task_attempt a ON a.expected_task_id = t.expected_task_id
			     WHERE t.task_key = 'LOAD_ASSERTIONS'
			       AND t.plan_status = 'DUE'
			       AND a.entity_resolution_arguments_total IS NOT NULL
			       AND a.entity_resolution_arguments_resolved IS NOT NULL
			       AND a.execution_status = 'SUCCEEDED'
			       AND a.record_source = 'WRAPPER'
			       AND a.started_at IS NOT NULL
			       AND a.finished_at IS NOT NULL
			       AND %s <= COALESCE(CAST(? AS date),
			                          (now() AT TIME ZONE 'Asia/Seoul')::date)
			), recent AS (
			    SELECT d, total_arguments, resolved_arguments
			      FROM ranked
			     WHERE rn = 1
			     ORDER BY d DESC
			     LIMIT 10
			)
			SELECT d, total_arguments, resolved_arguments
			  FROM recent
			 ORDER BY d
			""".formatted(RUN_DAY, RUN_DAY, RUN_DAY);

	/**
	 * 장중 발화의 일별 트리거 수(ALPHA-1330). 날짜 범위의 날마다 한 행이고, 발화가 없던 날은 0 이다.
	 *
	 * <p>트리거의 정의는 체인 입력({@link #FEEDS_SQL} 의 {@code feed_intraday})과 같다 — {@code FIRE} 만
	 * 세고, 날짜는 {@code window_start} 의 KST 날짜다. 범위는 시각 열에 직접 건다. 단계 수는 날짜마다
	 * {@link #analysisStages} 가 센다. 옛 설명 엔진의 표를 따라가던 단계(관측·실행·결과·게시)는 지웠다.
	 */
	private static final String INTRADAY_ANALYSIS_TREND_SQL = """
			WITH days AS (
			    SELECT generate_series(CAST(? AS date) - (? - 1), CAST(? AS date),
			                           interval '1 day')::date AS d
			), trigger_cohort AS (
			    SELECT m.trigger_id,
			           (m.window_start AT TIME ZONE 'Asia/Seoul')::date AS d
			      FROM minute_price_trigger m
			     WHERE m.trigger_kind = 'FIRE'
			       AND m.window_start >= ((CAST(? AS date) - (? - 1))::timestamp
			                              AT TIME ZONE 'Asia/Seoul')
			       AND m.window_start < ((CAST(? AS date) + 1)::timestamp
			                             AT TIME ZONE 'Asia/Seoul')
			)
			SELECT now() AS as_of, d.d, count(c.trigger_id) AS triggers
			  FROM days d
			  LEFT JOIN trigger_cohort c ON c.d = d.d
			 GROUP BY d.d
			 ORDER BY d.d
			""";

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

	/**
	 * 기준(중앙값)을 잴 <b>직전 거래일</b> 후보.
	 *
	 * <p>⚠️ <b>{@code trading_date} 는 거래일 달력이 아니다.</b> Planner 는 슬롯 날짜를 그대로 쓴다
	 * ({@code ops/planner.py} 의 {@code plan_run} 이 {@code day = slot.date()} 를 그대로
	 * {@code trading_date} 로 넘긴다 — 거래일인지 안 본다). 휴장 판정은 기대 작업 쪽에
	 * {@code skip_reason='NON_TRADING_DAY'} 로 남고, 주말은 아예 신호가 없다({@link #marketDay}).
	 *
	 * <p>🔴 <b>여기서 10개로 자르지 않는다.</b> 자르고 나서 휴장일을 빼면 그중 하나가 휴장일일 때
	 * 표본이 9개로 줄고 <b>11번째 거래일은 영영 안 들어온다</b>. 자르는 것은 {@link #baseDays} 가
	 * 합집합·제외를 다 끝낸 뒤에 한다({@code slotDays} 가 같은 이유로 LIMIT 을 안 쓰는 것과 한 짝이다).
	 */
	private static final String BASE_DAYS_SQL = """
			SELECT DISTINCT %s AS d
			  FROM ops_pipeline_run r
			 WHERE %s <= ?
			 ORDER BY 1 DESC
			""".formatted(RUN_DAY, RUN_DAY);

	/**
	 * 휴장일 — 그날 어느 런이든 KR 시장 작업을 {@code NON_TRADING_DAY} 로 건너뛰었으면 휴장이다
	 * ({@code ops/planner.py} 의 {@code skip = (not trading) and entry.kr_trading_calendar}).
	 *
	 * <p>🔴 <b>제외는 런 단위가 아니라 날짜 단위다.</b> 휴장 신호는 {@code kr_trading_calendar=true}
	 * 인 <b>작업</b>에만 붙는다 — 지금은 가격 레인 셋과 장중 수급 레인 둘이고({@code ops/catalog.py};
	 * 같은 레인 안에서도 작업마다 다르다), 뉴스·공시 레인은 휴장일에도 돈다. 런 단위로 상관시키면
	 * 그 레인의 런이 <b>같은 날짜를 다시 표본에 넣는다</b>. 그래서 날짜 집합으로 뽑는다.
	 *
	 * <p>{@link #baseDays} 가 <b>합집합 뒤에 한 번</b> 빼는 이유는 집합 대수가 아니라(양쪽에서 빼도
	 * 결과는 같다) <b>빠뜨림을 구조적으로 막기 위해서</b>다 — 한 소스에서만 빼면 다른 소스가 같은
	 * 날짜를 되살린다. 합집합 뒤 한 자리면 소스가 늘어도 자동으로 덮인다.
	 *
	 * <p>⚠️ 이 신호는 <b>평일 휴장만</b> 답한다. 주말은 {@link #marketDay} 가 달력으로 답한다.
	 *
	 * <p>⚠️ <b>리터럴을 {@code IS NOT NULL} 로 바꾸는 변이는 테스트로 못 잡는다</b>(변이 검증에서
	 * 생존). {@code skip_reason} 에 들어가는 값이 지금 <b>하나뿐</b>이라 두 술어가 동치이기 때문이다
	 * ({@code ops/states.py} 에 {@code SKIP_NON_TRADING_DAY} 만 있고 쓰는 곳은 {@code planner.py} 의
	 * 한 자리다). 죽이려면 프로듀서가 안 쓰는 값을 픽스처가 지어내야 해서 그러지 않았다.
	 * 그래도 <b>리터럴을 유지한다</b> — 둘째 사유(예: 상류 결손)가 생기는 날 {@code IS NOT NULL} 은
	 * 그것까지 조용히 휴장으로 세고, 그러면 그 날의 산출이 표본에서 통째로 빠진다.
	 */
	private static final String HOLIDAY_DAYS_SQL = """
			SELECT DISTINCT %s AS d
			  FROM ops_pipeline_run r
			  JOIN ops_expected_task t ON t.pipeline_run_id = r.pipeline_run_id
			 WHERE %s <= ?
			   AND t.skip_reason = 'NON_TRADING_DAY'
			""".formatted(RUN_DAY, RUN_DAY);

	/**
	 * 산출 축의 명세 — {@code id}·{@code label}·{@code unit} 은 소비자 어휘와 1:1 이고 {@code sql} 은
	 * <b>날짜별 카운트</b>를 낸다({@code %s} 자리에 날짜 플레이스홀더가 들어간다).
	 *
	 * <p>{@code analysisAtRange} 가 참이면 SQL 이 날짜 목록 앞에 <b>시각 범위 둘</b>
	 * ({@code analysis_at >= ? AND analysis_at < ?})을 더 받는다(ALPHA-1290). v2 분석 표는 날짜 컬럼이
	 * 아니라 {@code analysis_at}(timestamptz)이 축이라, KST 날짜 식만으로 거르면 열에 함수가 씌어
	 * 범위를 못 좁힌다. 범위는 조회한 날짜들의 처음부터 끝까지다.
	 *
	 * <p>{@code marketBound} 는 <b>이 산출이 장이 서야 나오는가</b>다. 휴장일에는 0 이 실측이 아니라
	 * "그날 나올 것이 아니었다"인데 응답에는 그 둘을 가르는 자리가 없다({@code today} 는 수 하나다).
	 * 그래서 그날은 <b>기준을 안 준다</b> — 소비자는 기준 없는 산출을 편차 판정에서 뺀다. 뉴스 갈래는
	 * 휴장일에도 도니까 해당 없다.
	 */
	private record OutputSpec(String id, String label, String unit, boolean marketBound,
			boolean analysisAtRange, String sql) {

		OutputSpec(String id, String label, String unit, boolean marketBound, String sql) {
			this(id, label, unit, marketBound, false, sql);
		}
	}

	/**
	 * 날짜 축이 <b>둘</b>이다 — 거래일 컬럼을 가진 산출({@code trade_date})과 수집 시각뿐인 산출
	 * ({@code available_at} 의 KST 날짜). 뒤 셋은 표현식 필터라 인덱스를 못 탄다
	 * ({@link JdbcNewsLineageRepository} 가 같은 이유로 이미 감수한 비용) — 콘솔 단발 조회 규모라
	 * 지금은 감당 범위지만, 여기는 누적 테이블 <b>셋</b>을 한 요청에서 훑는다. 느려지면 함수 인덱스가
	 * 먼저 붙을 자리다.
	 */
	private static final List<OutputSpec> OUTPUTS = List.of(
			/* 분석 산출은 v2 표에서 센다(ALPHA-1290). 옛 설명 엔진의 `explanation_result` 는 10-01
			 * 이 마지막 실행이라 그 표를 세던 옛 게시 ETF 산출은 계속 0 이었다.
			 *
			 * 🔴 **"게시"가 아니라 "완료"를 센다.** 전망은 완료될 때 항상 `published_at` 이 찍혀 둘이
			 * 같지만, 가격변동 분석은 고른 항목이 없으면 완료돼도 `published_at` 이 NULL 이고 회수되면
			 * `withdrawn_at` 이 찍힌다 — 완료가 곧 지금 보이는 게시본은 아니다. 이 산출은 "분석이 그날
			 * 그 ETF 에 대해 끝까지 돌았는가"를 답한다.
			 *
			 * `data_source = 'database'` 만 센다 — 합성 입력(`synthetic`)으로 돈 분석은 운영 산출이
			 * 아니다(v2 조회 API 가 같은 조건으로 거른다, `api/publications.py`).
			 *
			 * 전망은 주말·휴일에도 매일 돈다(`marketBound=false` — 휴장일에도 기준을 준다). 가격변동
			 * 분석은 장이 열린 날에만 생긴다(`marketBound=true`). */
			new OutputSpec("o.outlook", "전망 완료 ETF", "종", false, true, """
					SELECT (analysis_at AT TIME ZONE 'Asia/Seoul')::date AS d,
					       count(DISTINCT etf_code) AS n
					  FROM outlook_analyses
					 WHERE analysis_at >= ? AND analysis_at < ?
					   AND status = 'completed' AND data_source = 'database'
					   AND (analysis_at AT TIME ZONE 'Asia/Seoul')::date IN (%s)
					 GROUP BY 1"""),
			new OutputSpec("o.movement", "가격변동 분석 완료 ETF", "종", true, true, """
					SELECT (analysis_at AT TIME ZONE 'Asia/Seoul')::date AS d,
					       count(DISTINCT etf_code) AS n
					  FROM movement_analyses
					 WHERE analysis_at >= ? AND analysis_at < ?
					   AND status = 'completed' AND data_source = 'database'
					   AND (analysis_at AT TIME ZONE 'Asia/Seoul')::date IN (%s)
					 GROUP BY 1"""),
			new OutputSpec("o.trig", "배치 트리거", "종", true, """
					SELECT trade_date AS d, count(DISTINCT etf_instrument_id) AS n
					  FROM price_movement_trigger
					 WHERE trade_date IN (%s)
					 GROUP BY trade_date"""),
			new OutputSpec("o.doc", "뉴스 문서", "건", false, """
					SELECT (available_at AT TIME ZONE 'Asia/Seoul')::date AS d, count(*) AS n
					  FROM document
					 WHERE document_type = 'NEWS'
					   AND (available_at AT TIME ZONE 'Asia/Seoul')::date IN (%s)
					 GROUP BY 1"""),
			new OutputSpec("o.asr", "assertion", "건", false, """
					SELECT (available_at AT TIME ZONE 'Asia/Seoul')::date AS d, count(*) AS n
					  FROM document_assertion
					 WHERE (available_at AT TIME ZONE 'Asia/Seoul')::date IN (%s)
					 GROUP BY 1"""),
			/* ⚠️ {@code event_status}(ACTIVE·REJECTED) 축은 <b>안 거른다</b>. 오늘은 무해하다 —
			 * 프로듀서 둘 다 {@code ACTIVE} 를 박고 REJECTED 를 쓰는 writer 가 레포에 없다(dev 실측
			 * 으로도 비-ACTIVE 0건). REJECTED 적재가 생기면 이 수의 뜻이 조용히 바뀐다. */
			new OutputSpec("o.evt", "source event", "건", false, """
					SELECT (available_at AT TIME ZONE 'Asia/Seoul')::date AS d, count(*) AS n
					  FROM source_event
					 WHERE (available_at AT TIME ZONE 'Asia/Seoul')::date IN (%s)
					 GROUP BY 1"""));

	/**
	 * 경계 정합 — 세 카운트를 <b>한 문장</b>으로 낸다. 쪼개면 조회 사이에 writer 가 끼어 <b>존재한
	 * 적 없는 조합</b>이 조립된다(뉴스 계보 요약과 같은 이유). 격리수준이 지켜 주는 것과 별개로,
	 * 세 수가 서로를 설명하는 한 벌이라 한 문장이 그 관계를 코드에 남긴다.
	 *
	 * <p><b>새 분석 엔진(v2)의 가격변동 분석을 본다</b>(ALPHA-1326). 옛 설명 엔진의
	 * {@code explanation_result} 는 10-01 이 마지막 게시라 그 조인으로는 지금의 게시를 못 봤다(#1256 이
	 * 그래서 관측 불가로 내렸다). v2 는 같은 {@code tenant_delivery} 에 쓴다 — 게시 전달(NEW)은
	 * {@code movement_analysis_id}, 회수 전달(INVALIDATION)은 {@code target_movement_analysis_id} 다.
	 * 전망({@code outlook_analyses})은 테넌트에 전달하지 않는다(이 표에 전망을 가리키는 열이 없고 v2 에
	 * 그 writer 가 없다). {@code data_source = 'database'} 만 센다.
	 *
	 * <p><b>게시됐는데 전달 없음</b> — 지금 게시본(완료 ∧ {@code published_at} 있음 ∧ 회수 안 됨)인데
	 * 그 분석을 가리키는 NEW 가 없는 것. 단, v2 가 <b>설계상 전달하지 않는</b> 경우를 뺀다. 전달은
	 * {@code storage/delivery.py} 의 {@code enqueue_movement} 하나가 하고, 거기서 NEW 를 안 만드는
	 * 갈래가 넷이다:
	 * <ol>
	 *   <li>전달 시점에 이미 회수됐다 — {@code withdrawn_at} 을 채우고 끝난다. 지금 게시본이 아니라
	 *       애초에 이 수에 안 든다.</li>
	 *   <li>같은 ETF 에 같거나 더 늦은 분석이 이미 전달됐다({@code delivered.analysis_at >=
	 *       current.analysis_at}).</li>
	 *   <li>같은 거래일·같은 게시 시각·같은 고른 항목의 분석이 이미 전달됐다 — 직전 분석의 게시를
	 *       그대로 물려받은 경우다({@code storage/publications.py} 가 고른 항목이 같으면 직전의
	 *       {@code published_at} 을 물려준다).</li>
	 *   <li>테넌트가 한 곳도 없다 — 전 테넌트에 넣는 문장이 0행이다.</li>
	 * </ol>
	 * 2·3 은 그 함수의 조건을 <b>그대로</b> 옮겼다. 지금 시점에 평가하므로, 전달이 실패한 분석이라도
	 * 뒤에 같은 ETF 의 더 늦은 분석이 전달됐으면 빠진다 — 테넌트는 그 ETF 의 최신 게시본을 받았다.
	 *
	 * <p><b>전달 대기 시간</b> — 전달은 게시와 같은 분석 작업 안에서 게시 직후에 일어난다
	 * ({@code cloud/worker.py}). 그래서 그 작업이 끝나기 전의 분석은 아직 판정하지 않는다. 작업은 단일
	 * 분석 워크플로 안에서 돌고 그 워크플로는 시작 뒤 {@link #ANALYSIS_GRACE_SECONDS} 를 못 넘긴다.
	 * 요청이 수용된 시각({@code accepted_at})은 워크플로 시작보다 늦으므로, 그 시각에서 그만큼이 지나야
	 * 전달이 없다고 말할 수 있다. 게시 시각만 쓰지 않는 이유: 물려받은 게시 시각은 직전 분석의 것이라
	 * 지금 도는 작업보다 앞선다. 요청은 있는데 수용 기록이 없으면(실행은 떴는데 수용 기록이 실패한
	 * 경우 — {@code cloud/admission.py} 는 실행을 띄운 뒤 수용을 기록한다) 요청이 만들어진 시각과 게시
	 * 시각 중 늦은 쪽을 쓰고, 요청이 없으면 게시 시각을 쓴다.
	 *
	 * <p><b>전달됐는데 지금 게시본이 아니고 회수 전달도 없음</b> — 테넌트별 NEW 행 단위다(옛 수와 같은
	 * 단위). 회수({@code storage/retractions.py} 의 {@code retract_movement})는 {@code withdrawn_at} 을
	 * 채우는 문장과 INVALIDATION 을 넣는 문장이 한 트랜잭션이라 대기 시간이 없다. 회수됐는데 전달된 적이
	 * 없는 분석은 회수 전달을 만들지 않는 것이 설계다 — 이 수는 NEW 가 있는 분석만 보므로 애초에 안 든다.
	 * 무효화 통지와의 상관은 결과만이 아니라 <b>테넌트까지</b>다(받고도 통지 못 받은 테넌트가 있으면
	 * 위반이다).
	 *
	 * <p>⚠️ <b>{@code delivery_type} 을 따로 거르지 않는다.</b> {@code ck_tenant_delivery_payload} 가
	 * {@code movement_analysis_id} 를 NEW 에만, {@code target_movement_analysis_id} 를 INVALIDATION 에만
	 * 허용한다 — 유형 술어를 덧붙이면 어떤 행도 못 거르는 가드가 하나 는다.
	 *
	 * <p>⚠️ 옛 엔진의 게시·전달은 더는 세지 않는다. 그 엔진 분에 어긋남이 남아 있어도 이 축에는 안
	 * 보인다(마지막 게시가 10-01 이다).
	 *
	 * <p>⚠️ <b>날짜 창을 안 탄다</b> — 인터페이스 {@link BoundaryRow} 주석의 이유. 이 수들의 기준
	 * 시각은 {@code meta.today} 가 아니라 <b>{@code meta.db}</b>(DB 시계)다.
	 */
	private static final String BOUNDARY_SQL = """
			SELECT (SELECT count(*) FROM movement_analyses a
			         WHERE a.data_source = 'database'
			           AND a.status = 'completed'
			           AND a.published_at IS NOT NULL
			           AND a.withdrawn_at IS NULL
			           AND EXISTS (SELECT 1 FROM tenant)
			           AND COALESCE((SELECT COALESCE(q.accepted_at, GREATEST(q.created_at, a.published_at))
			                           FROM analysis_execution_requests q
			                          WHERE q.analysis_id = a.analysis_id), a.published_at)
			               <= now() - (? * interval '1 second')
			           AND NOT EXISTS (SELECT 1 FROM tenant_delivery d
			                             JOIN movement_analyses delivered
			                               ON delivered.analysis_id = d.movement_analysis_id
			                            WHERE delivered.etf_code = a.etf_code
			                              AND (delivered.analysis_id = a.analysis_id
			                                   OR delivered.analysis_at >= a.analysis_at
			                                   OR (delivered.trading_date = a.trading_date
			                                       AND delivered.published_at = a.published_at
			                                       AND delivered.selected_item_ids
			                                           = a.selected_item_ids))))
			         AS published_without_delivery,
			       (SELECT count(*) FROM tenant_delivery d
			          JOIN movement_analyses a ON a.analysis_id = d.movement_analysis_id
			         WHERE a.data_source = 'database'
			           AND NOT (a.status = 'completed' AND a.published_at IS NOT NULL
			                    AND a.withdrawn_at IS NULL)
			           AND NOT EXISTS (SELECT 1 FROM tenant_delivery inv
			                            WHERE inv.tenant_id = d.tenant_id
			                              AND inv.target_movement_analysis_id
			                                  = d.movement_analysis_id)) AS delivery_now_nonpublished,
			       (SELECT count(*) FROM tenant_delivery) AS delivery_rows
			""";

	/**
	 * 체인의 입력 두 갈래 — 조회일에 생긴 배치 트리거 수와 장중 발화(FIRE) 수(ALPHA-1329).
	 *
	 * <p>옛 설명 엔진의 다섯 단계를 함께 세던 조회({@code CHAIN_SQL})에서 입력 부분만 남겼다. 갈래의
	 * 정의는 그대로다. <b>분봉 트리거는 {@code FIRE} 만 센다</b> — {@code REVERT} 는 노출 회수
	 * 마커라 분석을 만들지 않는다.
	 */
	private static final String FEEDS_SQL = """
			SELECT (SELECT count(*) FROM price_movement_trigger WHERE trade_date = ?) AS feed_batch,
			       (SELECT count(*) FROM minute_price_trigger
			         WHERE trigger_kind = 'FIRE'
			           AND (window_start AT TIME ZONE 'Asia/Seoul')::date = ?) AS feed_intraday
			""";

	/**
	 * 새 분석 엔진의 단계 하나 — {@code column} 은 {@link #ANALYSIS_CHAIN_SQL} 의 컬럼이다.
	 * {@code label}·{@code src} 를 서버가 내는 것은 산출 축({@link OutputSpec})과 같은 규약이다.
	 */
	private record StageSpec(String id, String label, String src, String column) {
	}

	/**
	 * 두 갈래의 입력. <b>순서가 계약이다</b> — 소비자가 {@code feeds[0]}=배치·{@code feeds[1]}=장중
	 * 으로 위치를 읽고, 그 값을 각 갈래의 첫 비교점으로 쓴다(R10·{@code ChainStrip}).
	 *
	 * <p>단위가 갈린다: 배치는 ETF 하나에 하루 한 행이고({@code uq_price_movement_trigger} 가
	 * {@code (etf, trade_date, detected_at)} 이라 <b>보장은 아니다</b> — {@code detected_at} 이
	 * 결정적 15:30 이라 실질 하나다), 장중은 같은 종목이 하루에 여러 번 발화한다.
	 */
	private static final List<FeedSpec> CHAIN_FEEDS = List.of(
			new FeedSpec("feed.batch", "배치 트리거", "ETF", "price_movement_trigger", "feed_batch"),
			new FeedSpec("feed.intraday", "장중 트리거", "건", "minute_price_trigger",
					"feed_intraday"));

	/** {@code column} 은 {@link #FEEDS_SQL} 의 컬럼 이름 그대로다. */
	private record FeedSpec(String id, String label, String unit, String src, String column) {
	}

	private final JdbcTemplate jdbc;

	public JdbcConsoleFactsRepository(JdbcTemplate jdbc) {
		this.jdbc = jdbc;
	}

	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public ConsoleFacts facts(LocalDate date) {
		/* 구간별 소요 시간(ALPHA-1314) — 재기만 한다. 조회문·순서·트랜잭션은 그대로이고, 각 조회를
		 * `timer.time` 으로 감쌌을 뿐이다. 구간의 정의는 `FactsSegment` 에 있다. */
		FactsSegment.Timer timer = new FactsSegment.Timer();
		Meta meta = timer.time(FactsSegment.META, () -> jdbc.queryForObject(META_SQL, (rs, i) -> new Meta(
				rs.getObject("db_now", OffsetDateTime.class),
				rs.getObject("kst_today", LocalDate.class),
				rs.getObject("run_latest", LocalDate.class))));
		LocalDate day = date != null ? date : timer.time(FactsSegment.META, () -> latestDay(meta));

		/* 두 소스가 한 축으로 합쳐진다 — 실재하는 런과, 런 행이 없는 계획 슬롯. 합친 뒤 다시
		 * 정렬하는 이유: 각 소스가 자기 안에서만 정렬돼 있어 그냥 이어 붙이면 전체 순서가 깨진다. */
		List<RunRow> runs = new ArrayList<>(timer.time(FactsSegment.RUNS_DB,
				() -> jdbc.query(RUNS_SQL, JdbcConsoleFactsRepository::mapRun, day)));
		timer.time(FactsSegment.RUNS_DB,
				() -> jdbc.queryForList(MISSING_SLOTS_SQL, String.class, day.toString()))
				.forEach(runKey -> runs.add(missingSlot(runKey)));
		/* 순서의 계약은 여기 하나다 — SQL 쪽에도 두면 둘이 서로를 가린다(RUNS_SQL 주석).
		 * `run_key` 는 UNIQUE 라(`uq_ops_pipeline_run_key`) 이 키 하나로 전순서가 정해진다. */
		runs.sort(Comparator.comparing(RunRow::runKey));

		/* 인자는 왼쪽부터 차례로 평가된다 — 감싸기 전과 같은 조회 순서다. 구간 시간은 마지막 인자라
		 * 앞의 조회가 모두 끝난 뒤의 값이다. */
		return new ConsoleFacts(day, meta.dbNow(), List.copyOf(runs),
				timer.time(FactsSegment.TASKS,
						() -> jdbc.query(TASKS_SQL, JdbcConsoleFactsRepository::mapTask, day)),
				timer.time(FactsSegment.OUTPUTS, () -> outputs(day, meta.kstToday())),
				timer.time(FactsSegment.BOUNDARY,
						() -> jdbc.queryForObject(BOUNDARY_SQL, (rs, i) -> new BoundaryRow(
								rs.getLong("published_without_delivery"),
								rs.getLong("delivery_now_nonpublished"),
								rs.getLong("delivery_rows")), ANALYSIS_GRACE_SECONDS)),
				withAnalysisStages(timer.time(FactsSegment.FEEDS,
						() -> jdbc.queryForObject(FEEDS_SQL, JdbcConsoleFactsRepository::mapFeeds, day, day)),
						day, timer),
				timer.time(FactsSegment.ANALYSIS_FAILURES, () -> analysisFailures(day)),
				timer.time(FactsSegment.ETF_ANALYSES, () -> etfAnalyses(day)),
				timer.nanos());
	}

	private List<EtfAnalysisStateRow> etfAnalyses(LocalDate day) {
		OffsetDateTime from = day.atStartOfDay(KST).toOffsetDateTime();
		OffsetDateTime to = day.plusDays(1).atStartOfDay(KST).toOffsetDateTime();
		return jdbc.query(ETF_ANALYSES_SQL, (rs, i) -> new EtfAnalysisStateRow(
				rs.getString("etf_code"), rs.getString("name"),
				rs.getString("outlook"), rs.getString("movement")),
				from, to, from, to, ANALYSIS_GRACE_SECONDS, from, from, to);
	}

	private List<AnalysisFailureRow> analysisFailures(LocalDate day) {
		OffsetDateTime from = day.atStartOfDay(KST).toOffsetDateTime();
		OffsetDateTime to = day.plusDays(1).atStartOfDay(KST).toOffsetDateTime();
		return jdbc.query(ANALYSIS_FAILURES_SQL, (rs, i) -> new AnalysisFailureRow(
				rs.getString("kind"), rs.getString("etf_code")), from, to, from, to);
	}

	@Override
	@Transactional(readOnly = true)
	public List<EntityResolutionPoint> entityResolutionTrend(LocalDate date) {
		return jdbc.query(ENTITY_RESOLUTION_TREND_SQL, (rs, i) -> new EntityResolutionPoint(
				rs.getObject("d", LocalDate.class),
				rs.getLong("total_arguments"),
				rs.getLong("resolved_arguments")), date);
	}

	/**
	 * v2 분석의 일별 건수(ALPHA-1290). 범위는 {@code analysis_at} 에 직접 건다 — KST 날짜 식은 묶는
	 * 데만 쓴다. {@code data_source = 'database'} 만 센다(산출 축과 같은 이유 — 합성 입력으로 돈
	 * 분석은 운영 산출이 아니다).
	 *
	 * <p>ETF 종 수는 ETF 별로 먼저 묶은 뒤에 센다 — {@code failed_etfs} 는 그날 실패 행이 있고 완료 행이
	 * 하나도 없는 ETF 수다(ALPHA-1291). 실패했다가 같은 날 다시 돌아 완료된 ETF 는 빠진다.
	 *
	 * <p>🔴 {@code running} 을 {@code failed} 에 더하지 않는다. 진행 중인 분석과 끝나지 못하고 남은
	 * 행이 같은 상태값이라, 실패로 세면 분석이 도는 동안 실패 수가 올라갔다 내려간다.
	 */
	private static final String ANALYSIS_TREND_SQL = """
			SELECT 'outlook' AS kind, d,
			       sum(completed) AS completed, sum(failed) AS failed, sum(running) AS running,
			       count(*) FILTER (WHERE completed > 0) AS completed_etfs,
			       count(*) FILTER (WHERE failed > 0 AND completed = 0) AS failed_etfs
			  FROM (SELECT (analysis_at AT TIME ZONE 'Asia/Seoul')::date AS d, etf_code,
			               count(*) FILTER (WHERE status = 'completed') AS completed,
			               count(*) FILTER (WHERE status = 'failed') AS failed,
			               count(*) FILTER (WHERE status = 'running') AS running
			          FROM outlook_analyses
			         WHERE analysis_at >= ? AND analysis_at < ? AND data_source = 'database'
			         GROUP BY 1, 2) per_etf
			 GROUP BY d
			UNION ALL
			SELECT 'movement' AS kind, d,
			       sum(completed) AS completed, sum(failed) AS failed, sum(running) AS running,
			       count(*) FILTER (WHERE completed > 0) AS completed_etfs,
			       count(*) FILTER (WHERE failed > 0 AND completed = 0) AS failed_etfs
			  FROM (SELECT (analysis_at AT TIME ZONE 'Asia/Seoul')::date AS d, etf_code,
			               count(*) FILTER (WHERE status = 'completed') AS completed,
			               count(*) FILTER (WHERE status = 'failed') AS failed,
			               count(*) FILTER (WHERE status = 'running') AS running
			          FROM movement_analyses
			         WHERE analysis_at >= ? AND analysis_at < ? AND data_source = 'database'
			         GROUP BY 1, 2) per_etf
			 GROUP BY d
			 ORDER BY 1, 2
			""";

	/**
	 * 조회일에 v2 분석이 끝내 완료되지 못한 ETF(R15, ALPHA-1291) — 그날 그 종류의 분석에 실패 행이
	 * 있고 완료 행이 하나도 없는 ETF 다. 분석 추이의 {@code failed_etfs} 와 <b>같은 기준</b>이고,
	 * 그쪽이 수를 낼 때 여기는 어느 ETF 인지를 낸다.
	 *
	 * <p>running 만 있는 ETF 는 넣지 않는다 — 진행 중인지 끝나지 못한 것인지 이 표만으로 못 가른다.
	 * 날짜는 {@code analysis_at} 의 KST 날짜라, 자정 직전에 실패하고 자정 뒤에 다시 돌아 완료된 ETF 는
	 * 앞 날짜의 실패로 남는다.
	 */
	private static final String ANALYSIS_FAILURES_SQL = """
			SELECT 'outlook' AS kind, etf_code
			  FROM outlook_analyses
			 WHERE analysis_at >= ? AND analysis_at < ? AND data_source = 'database'
			 GROUP BY etf_code
			HAVING count(*) FILTER (WHERE status = 'failed') > 0
			   AND count(*) FILTER (WHERE status = 'completed') = 0
			UNION ALL
			SELECT 'movement' AS kind, etf_code
			  FROM movement_analyses
			 WHERE analysis_at >= ? AND analysis_at < ? AND data_source = 'database'
			 GROUP BY etf_code
			HAVING count(*) FILTER (WHERE status = 'failed') > 0
			   AND count(*) FILTER (WHERE status = 'completed') = 0
			 ORDER BY 1, 2
			""";

	/**
	 * 조회일의 ETF별 v2 분석 귀결(ALPHA-1312) — 흐름 화면의 ETF 표가 그린다. ETF 하나에 전망과
	 * 가격변동 분석 각각의 그날 상태가 온다. 이 조회는 <b>보여 주기만 한다</b> — 판정은 R15·R10 이 한다.
	 *
	 * <p><b>행</b>: 그날 전망 분석 행이 있거나, 가격변동 분석 행이 있거나, 장중 트리거가 발화한 ETF 다.
	 * 셋 다 없는 ETF 는 나오지 않는다 — 전망 배치의 대상 목록은 DB 에 없어(배치 정의에 박힌 Terraform
	 * 변수다) "대상인데 빠진 ETF"를 이 조회가 알 수 없다.
	 *
	 * <p><b>종류별 상태</b>는 이 순서로 정한다:
	 * <ol>
	 *   <li>{@code completed} — 그날 완료 행이 하나라도 있다.</li>
	 *   <li>{@code failed} — 실패 행이 있고 완료 행이 없다. {@link #ANALYSIS_FAILURES_SQL} 과 <b>같은
	 *       기준</b>이라 이 표의 실패 ETF 와 R15 의 목록이 같다.</li>
	 *   <li>{@code running} — 진행 중인 행만 있다.</li>
	 *   <li>(가격변동 분석만) 그날 그 ETF 의 트리거에서 이어진 수용된 요청이 있는데 분석 행이 없다.
	 *       그런 <b>트리거</b> 가운데 유예 밖인 것이 하나라도 있으면 {@code not_started}, 모두 유예
	 *       안이면 {@code pending}. 단위가 요청이 아니라 트리거다 — 한 트리거에 요청이 여럿(재발화
	 *       세대)이면 가장 늦게 수용된 요청이 유예 안인 동안 그 트리거는 유예 안이다. 흐름 띠가 그렇게
	 *       세므로 여기서 요청 단위로 가르면 띠는 판정 보류, 표는 시작 못 함으로 갈린다.
	 *       기준은 {@link #ANALYSIS_CHAIN_SQL} 의 {@code started_pending} 과 같다 — 같은 상수
	 *       ({@link #ANALYSIS_GRACE_SECONDS}), 같은 기점(요청의 {@code accepted_at}), 같은 연결
	 *       (요청 입력의 {@code source.trigger_id}).</li>
	 *   <li>{@code none} — 위 어느 것도 아니다. 트리거는 발화했는데 수용된 요청이 없는 ETF 도 여기에
	 *       든다(그 감소는 R10 의 트리거 → 분석 요청이 센다).</li>
	 * </ol>
	 * 분석 행의 날짜는 {@code analysis_at} 의 KST 날짜이고, 트리거의 날짜는 {@code window_start} 다.
	 * {@code data_source = 'database'} 만 센다.
	 *
	 * <p><b>이름</b>은 옛 ETF 표와 같은 출처다 — v2 의 {@code etf_code} 는 종목 코드(트리거의
	 * {@code entity_id})라 {@code instrument(market_code='XKRX', ticker)} 로 {@code instrument_id} 를
	 * 얻고 {@code entity.display_name} 을 읽는다. 이어지지 않는 코드는 코드 그대로 낸다.
	 */
	private static final String ETF_ANALYSES_SQL = """
			WITH outlook AS (
			    SELECT etf_code,
			           CASE WHEN count(*) FILTER (WHERE status = 'completed') > 0 THEN 'completed'
			                WHEN count(*) FILTER (WHERE status = 'failed') > 0 THEN 'failed'
			                ELSE 'running' END AS state
			      FROM outlook_analyses
			     WHERE analysis_at >= ? AND analysis_at < ? AND data_source = 'database'
			     GROUP BY etf_code
			), movement AS (
			    SELECT etf_code,
			           CASE WHEN count(*) FILTER (WHERE status = 'completed') > 0 THEN 'completed'
			                WHEN count(*) FILTER (WHERE status = 'failed') > 0 THEN 'failed'
			                ELSE 'running' END AS state
			      FROM movement_analyses
			     WHERE analysis_at >= ? AND analysis_at < ? AND data_source = 'database'
			     GROUP BY etf_code
			), fired AS (
			    SELECT t.trigger_id, t.entity_id AS etf_code,
			           bool_or(q.analysis_id IS NOT NULL) AS requested,
			           bool_or(a.analysis_id IS NOT NULL) AS started,
			           COALESCE(bool_or(q.accepted_at > now() - (? * interval '1 second')), false)
			               AS request_young
			      FROM minute_price_trigger t
			      LEFT JOIN analysis_execution_requests q
			             ON q.created_at >= ?
			            AND q.accepted_at IS NOT NULL
			            AND q.input_json::jsonb -> 'source' ->> 'trigger_id' = t.trigger_id
			      LEFT JOIN movement_analyses a
			             ON a.analysis_id = q.analysis_id AND a.data_source = 'database'
			     WHERE t.trigger_kind = 'FIRE'
			       AND t.window_start >= ? AND t.window_start < ?
			     GROUP BY t.trigger_id, t.entity_id
			), unstarted AS (
			    SELECT etf_code,
			           bool_or(requested AND NOT started AND NOT request_young) AS lost,
			           bool_or(requested AND NOT started AND request_young) AS held
			      FROM fired
			     GROUP BY etf_code
			), codes AS (
			    SELECT etf_code FROM outlook
			    UNION SELECT etf_code FROM movement
			    UNION SELECT etf_code FROM unstarted
			)
			SELECT c.etf_code,
			       COALESCE(e.display_name, c.etf_code) AS name,
			       COALESCE(o.state, 'none') AS outlook,
			       COALESCE(m.state,
			                CASE WHEN u.lost THEN 'not_started' WHEN u.held THEN 'pending' END,
			                'none') AS movement
			  FROM codes c
			  LEFT JOIN outlook o ON o.etf_code = c.etf_code
			  LEFT JOIN movement m ON m.etf_code = c.etf_code
			  LEFT JOIN unstarted u ON u.etf_code = c.etf_code
			  LEFT JOIN instrument i ON i.market_code = 'XKRX' AND i.ticker = c.etf_code
			  LEFT JOIN entity e ON e.entity_id = i.instrument_id
			 ORDER BY c.etf_code
			""";

	@Override
	@Transactional(readOnly = true)
	public List<AnalysisDayPoint> analysisTrend(LocalDate maxDate, int days) {
		OffsetDateTime from = maxDate.minusDays(days - 1L).atStartOfDay(KST).toOffsetDateTime();
		OffsetDateTime to = maxDate.plusDays(1).atStartOfDay(KST).toOffsetDateTime();
		return jdbc.query(ANALYSIS_TREND_SQL, (rs, i) -> new AnalysisDayPoint(
				rs.getString("kind"), rs.getObject("d", LocalDate.class),
				rs.getLong("completed"), rs.getLong("failed"), rs.getLong("running"),
				rs.getLong("completed_etfs"), rs.getLong("failed_etfs")), from, to, from, to);
	}

	/**
	 * 🔴 <b>REPEATABLE READ 한 스냅샷이다</b>(ALPHA-1329). 트리거 수와 날짜별 v2 단계를 다른 문장으로 세므로,
	 * 그 사이에 트리거나 요청이 커밋되면 "트리거 0 · 요청 1" 같은 존재한 적 없는 조합이 나간다 — 화면은
	 * 단계 불변식이 깨진 응답을 통째로 거부한다.
	 */
	@Override
	@Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
	public IntradayAnalysisTrend intradayAnalysisTrend(LocalDate maxDate, int days) {
		List<IntradayAnalysisPoint> points = new ArrayList<>();
		OffsetDateTime[] asOf = new OffsetDateTime[1];
		jdbc.query(INTRADAY_ANALYSIS_TREND_SQL, (RowCallbackHandler) rs -> {
			if (asOf[0] == null) {
				asOf[0] = rs.getObject("as_of", OffsetDateTime.class);
			}
			points.add(new IntradayAnalysisPoint(rs.getObject("d", LocalDate.class),
					rs.getLong("triggers"), List.of()));
		}, maxDate, days, maxDate, maxDate, days, maxDate);
		/* 새 분석 엔진의 단계는 날짜마다 흐름 띠와 <b>같은 조회</b>로 센다(ALPHA-1329) — 같은 날짜를
		 * 물으면 두 화면이 같은 수를 낸다. 하루 한 번씩이라 조회 수가 날짜 수만큼 는다. */
		return new IntradayAnalysisTrend(asOf[0], points.stream()
				.map(p -> new IntradayAnalysisPoint(p.date(), p.triggers(), analysisStages(p.date())))
				.toList());
	}

	/**
	 * 새 분석 엔진(v2)의 단계 — 조회일에 발화한 장중 트리거가 단계마다 몇 건 도달했나(ALPHA-1296).
	 *
	 * <p>코호트는 옛 체인과 같다: <b>그 날 발화한 트리거</b>이고, 뒤 단계는 그 트리거에서 이어진
	 * 것만 센다(그날 만들어진 요청·분석 전체가 아니다). 트리거와 요청은 요청 입력의
	 * {@code source.trigger_id} 로 잇는다 — v2 가 요청을 만들 때 원 트리거의 id 를 그대로 싣는다
	 * ({@code sources/triggers.py}). 요청과 분석은 {@code analysis_id} 가 같다.
	 *
	 * <p><b>단위는 트리거다.</b> 단계마다 "그 단계에 도달한 트리거 수"를 세므로 한 트리거에 요청이
	 * 여럿(재발화 세대)이어도 한 번만 세고, 단계가 내려가며 늘지 않는다.
	 *
	 * <p>트리거의 날짜 범위는 {@code window_start} 에 직접 건다. 요청은 트리거보다 먼저 만들어질 수
	 * 없으므로 {@code created_at} 하한으로 훑는 범위를 좁힌다(상한은 없다 — 늦게 수용된 요청도 그
	 * 트리거의 자손이다). {@code data_source = 'database'} 인 분석만 센다.
	 *
	 * <p><b>아직 판정할 수 없는 트리거</b>(ALPHA-1309): 유예 시간 안에 있어 다음 단계에 못 간 것이
	 * 손실도 도달도 아닌 트리거다. {@code req_pending} 은 트리거 행이 만들어진 지({@code created_at})
	 * 유예 시간이 안 지났고 요청이 아직 수용되지 않은 트리거, {@code started_pending} 은 요청이 수용된
	 * 지({@code accepted_at}) 유예 시간이 안 지났고 분석 행이 아직 없는 트리거다. 이미 도달한 트리거는
	 * 유예 안이어도 도달로 센다. 시각은 DB 시각({@code now()} — 트랜잭션 시작 시각이라 한 조회 안에서
	 * 같다)과 비교하고 조회일과 무관하다(어제 저녁의 트리거는 자정 뒤에도 유예 안일 수 있다).
	 *
	 * <p>⚠️ 유예의 기점은 창 시각({@code window_start})이 <b>아니다</b>. 워크플로의 시간 제한은 수용
	 * 시점부터 흐르고, 밀린 큐나 복구 경로는 오래된 창의 트리거를 뒤늦게 만들고 수용한다 — 창 시각으로
	 * 재면 그런 건이 돌고 있는 동안 손실로 세어진다(봇 리뷰가 짚었다).
	 *
	 * <p>🔴 <b>요청은 수용된 것만 센다</b>({@code accepted_at IS NOT NULL}). v2 는 워크플로를 띄우기
	 * 전에 요청 행을 먼저 예약하고, 수용이 확인돼야 {@code accepted_at} 을 채운다
	 * ({@code storage/requests.py}). 예약만 된 행까지 세면 워크플로가 뜨지도 않은 건이 "요청은 됐는데
	 * 분석이 시작 안 됨"으로 보인다 — 실제로는 트리거가 요청으로 이어지지 못한 것이다. 첫 요청일도
	 * 같은 기준이다(봇 리뷰가 짚었다).
	 */
	private static final String ANALYSIS_CHAIN_SQL = """
			WITH intraday_trigger AS (
			    SELECT trigger_id, created_at
			      FROM minute_price_trigger
			     WHERE trigger_kind = 'FIRE'
			       AND window_start >= ? AND window_start < ?
			), reached AS (
			    SELECT t.trigger_id,
			           t.created_at > now() - (? * interval '1 second') AS young,
			           COALESCE(bool_or(q.accepted_at > now() - (? * interval '1 second')), false)
			               AS request_young,
			           bool_or(q.analysis_id IS NOT NULL) AS requested,
			           bool_or(a.analysis_id IS NOT NULL) AS started,
			           COALESCE(bool_or(a.status = 'completed'), false) AS completed,
			           COALESCE(bool_or(a.status = 'completed' AND a.published_at IS NOT NULL), false)
			               AS published
			      FROM intraday_trigger t
			      LEFT JOIN analysis_execution_requests q
			             ON q.created_at >= ?
			            AND q.accepted_at IS NOT NULL
			            AND q.input_json::jsonb -> 'source' ->> 'trigger_id' = t.trigger_id
			      LEFT JOIN movement_analyses a
			             ON a.analysis_id = q.analysis_id AND a.data_source = 'database'
			     GROUP BY t.trigger_id, t.created_at
			)
			SELECT count(*) FILTER (WHERE requested) AS req,
			       count(*) FILTER (WHERE started) AS started,
			       count(*) FILTER (WHERE young AND NOT requested) AS req_pending,
			       count(*) FILTER (WHERE request_young AND NOT started) AS started_pending,
			       count(*) FILTER (WHERE completed) AS completed,
			       count(*) FILTER (WHERE published) AS published,
			       (SELECT min((created_at AT TIME ZONE 'Asia/Seoul')::date)
			          FROM analysis_execution_requests
			         WHERE accepted_at IS NOT NULL) AS first_request_day
			  FROM reached
			""";

	/**
	 * 아직 판정할 수 없는 트리거의 유예 시간(초) — 앞 단계에 도달한 지 이만큼 안 지난 트리거가 다음
	 * 단계에 못 갔으면 손실이 아니라 <b>판정 불가</b>로 센다(ALPHA-1309).
	 *
	 * <p><b>값의 출처</b>: 단일 분석 워크플로의 전체 시간 제한이다
	 * ({@code infra/terraform/modules/analysis-v2/single_analysis.asl.json} 의 최상위
	 * {@code "TimeoutSeconds": 28800}). 요청 하나가 분석 행 없이 머물 수 있는 가장 긴 시간이 이 값이다:
	 * <ul>
	 *   <li>자리 대기({@code Acquire → Capacity → Wait 30초 → Acquire})에는 횟수 상한이 없다 — 같은
	 *       종류·같은 ETF 는 한 번에 하나만 돌아, 앞 분석이 끝날 때까지 기다린다. 이 대기를 끊는 것은
	 *       워크플로 전체 시간 제한뿐이다.</li>
	 *   <li>분석 작업({@code Analyze})은 재시도 없이 한 번이고 1,100초에 끊긴다. 분석 행은 그 작업
	 *       안에서 원천 조회 뒤에 생긴다.</li>
	 * </ul>
	 * 시간 제한이 지나면 실행이 끝나 그 뒤로는 분석 행이 생길 수 없다. 같은 요청이 다시 전달돼도
	 * 이미 있는 실행을 새로 띄우지 않는다({@code cloud/admission.py}).
	 *
	 * <p>⚠️ 기점은 단계마다 다르고 창 시각({@code window_start})이 아니다({@link #ANALYSIS_CHAIN_SQL}):
	 * 분석 시작 단계는 요청이 수용된 시각({@code accepted_at}), 분석 요청 단계는 트리거 행이 만들어진
	 * 시각({@code created_at})이다. 이 값의 근거는 수용 뒤의 상한뿐이다 — 분석 요청 단계에는 같은 값을
	 * 빌려 쓴 것이고, 트리거 생성에서 수용까지(큐 전달)의 상한을 따로 구한 것이 아니다.
	 *
	 * <p>⚠️ {@code accepted_at} 은 워크플로가 실제로 시작된 시각과 다를 수 있다. 실행은 떴는데 수용
	 * 기록이 실패하면 재전달 때 그 시각이 찍힌다 — 그 사이 실행이 이미 끝났어도 그때부터 유예가 다시
	 * 흐른다(손실이 그만큼 늦게 선다). 실행 시작 시각은 DB 에 없다.
	 *
	 * <p>워크플로 정의의 시간 제한이 바뀌면 이 값도 같이 바꿔야 한다(두 곳을 잇는 자동 검사는 없다).
	 */
	private static final long ANALYSIS_GRACE_SECONDS = 28_800L;

	/** v2 단계의 명세 — <b>순서가 흐름</b>이다. {@code column} 은 {@link #ANALYSIS_CHAIN_SQL} 의 컬럼이다. */
	private static final List<StageSpec> ANALYSIS_STAGES = List.of(
			new StageSpec("c.req", "분석 요청", "analysis_execution_requests", "req"),
			new StageSpec("c.start", "분석 시작", "movement_analyses", "started"),
			new StageSpec("c.done", "분석 완료", "status=completed", "completed"),
			new StageSpec("c.pub", "게시", "published_at IS NOT NULL", "published"));

	/** 감소를 손실로 판정하는 단계 — 판정 불가 수({@code pending})가 있는 단계다. */
	private static final java.util.Set<String> PENDING_STAGES = java.util.Set.of("c.req", "c.start");

	/** 체인 입력에 v2 단계를 붙인다. 단계를 세는 일은 {@link #analysisStages} 가 한다. */
	private ChainRow withAnalysisStages(List<ChainFeed> feeds, LocalDate day, FactsSegment.Timer timer) {
		return new ChainRow(feeds, timer.time(FactsSegment.ANALYSIS_STAGES, () -> analysisStages(day)));
	}

	/**
	 * 조회일의 v2 단계(ALPHA-1296). 흐름 띠와 장중 분석 귀결 추이가 <b>같은 이 메서드</b>를 부른다 —
	 * 같은 날짜를 물으면 두 화면이 같은 수를 낸다(ALPHA-1329).
	 *
	 * <p>🔴 <b>v2 가 아직 없던 날은 관측 불가다</b> — 그날 코호트에 요청이 하나도 없고, 첫 v2 요청이
	 * 조회일보다 뒤다(요청이 아예 없어도 같다). 날짜 상수가 아니라 원장이 답한다. 요청이 하나라도
	 * 있으면 그날의 수는 실측이다. 첫 요청이 조회일과 같거나 앞인데 요청이 0 이면 그것은 <b>진짜
	 * 손실</b>이다(트리거는 있는데 요청이 안 만들어졌다) — 가리지 않는다.
	 */
	private List<AnalysisStage> analysisStages(LocalDate day) {
		OffsetDateTime from = day.atStartOfDay(KST).toOffsetDateTime();
		OffsetDateTime to = day.plusDays(1).atStartOfDay(KST).toOffsetDateTime();
		return jdbc.queryForObject(ANALYSIS_CHAIN_SQL, (rs, i) -> {
			LocalDate firstRequestDay = rs.getObject("first_request_day", LocalDate.class);
			boolean blind = rs.getLong("req") == 0
					&& (firstRequestDay == null || firstRequestDay.isAfter(day));
			List<AnalysisStage> out = new ArrayList<>();
			for (StageSpec s : ANALYSIS_STAGES) {
				/* 판정 불가 수는 판정하는 두 단계에만 있다 — 그 단계의 컬럼 이름에 `_pending` 을 붙인
				 * 컬럼이다. 분석 완료·게시는 감소를 판정하지 않으므로 0 이다. */
				long pending = PENDING_STAGES.contains(s.id()) ? rs.getLong(s.column() + "_pending") : 0L;
				// 배치 칸은 null — 배치 트리거는 v2 의 대상이 아니다(0 이 아니다).
				out.add(new AnalysisStage(s.id(), s.label(), s.src(), null,
						rs.getLong(s.column()), blind, pending));
			}
			return List.copyOf(out);
		}, from, to, ANALYSIS_GRACE_SECONDS, ANALYSIS_GRACE_SECONDS, from);
	}

	/** 입력 두 갈래 — 목록의 <b>순서는 스펙 상수가 정한다</b>({@link #CHAIN_FEEDS}). */
	private static List<ChainFeed> mapFeeds(ResultSet rs, int rowNum) throws SQLException {
		List<ChainFeed> feeds = new ArrayList<>();
		for (FeedSpec f : CHAIN_FEEDS) {
			feeds.add(new ChainFeed(f.id(), f.label(), f.unit(), f.src(), rs.getLong(f.column())));
		}
		return List.copyOf(feeds);
	}

	/**
	 * 산출별 <b>그 날의 값과 직전 거래일 중앙값</b>. 산출마다 조회 한 번이고, 오늘과 기준일을
	 * 한 문장으로 센다.
	 *
	 * <p>⚠️ <b>일관성을 주는 것은 이 문장 구성이 아니라 트랜잭션이다.</b> {@link #facts} 전체가
	 * REPEATABLE READ 라 나눠 세도 같은 스냅샷을 읽는다 — 산출 여섯이 서로 다른 문장인데도
	 * 서로 일관된 것이 그 증거다. 한 문장으로 세는 이유는 왕복을 줄이는 것뿐이고, <b>격리수준을
	 * 낮추면</b> 그때야 나눠 센 값들이 갈린다.
	 */
	private static final ZoneId KST = ZoneId.of("Asia/Seoul");

	private List<OutputRow> outputs(LocalDate day, LocalDate kstToday) {
		/* 휴장일 목록을 한 번만 읽어 두 자리가 같은 술어를 쓰게 한다 — 표본에서 빼는 자리와
		 * "오늘이 휴장인가"를 묻는 자리가 갈리면 이 파일이 이미 겪은 종류의 결함이 된다. */
		List<LocalDate> holidays = jdbc.queryForList(HOLIDAY_DAYS_SQL, LocalDate.class, day);
		boolean targetIsNonMarketDay = !marketDay(day, holidays);
		/* 🔴 <b>그 날의 적재 창이 아직 안 지났으면 비교 대상이 아니다</b>(ALPHA-946). 기준일 후보는
		 * {@code day} 미만이라 <b>적재 창이 지난</b> 하루의 값인데, {@code day} 가 KST 오늘이면 그
		 * 값은 아직 쌓이는 중이다 — 같은 축이 아닌 둘을 나눠 임계에 건다. dev 실측(08-11 14:27 KST)
		 * 에서 당시 산출 다섯 중 <b>넷</b>이 그 이유로 거짓 P1 이었다({@code o.trig} −100%·옛 게시 ETF 산출
		 * −71%·{@code o.asr} −29%·{@code o.evt} −59%). 미래 날짜를 400 으로 막는
		 * {@code ConsoleFactsService.parseDateParam} 이 같은 논거를 이미 쓴다 — 오늘은 그
		 * "아직"이 <b>부분</b>으로 오는 날이다.
		 *
		 * <p>⚠️ <b>"완결"이 아니라 "창이 지났다"이다.</b> 지난 날의 값도 <b>양방향으로</b> 움직인다:
		 * 분석 산출은 재실행으로 뒤에도 늘 수 있고, 뉴스 셋의
		 * {@code available_at} 은 writer 마다 뜻이 다르다 — 배치 둘({@code steps/assemble_events.py}
		 * 은 <b>{@code published_at}</b>, {@code steps/load_documents.py} 는 {@code fetched_at} 폴백
		 * {@code published_at})은 {@code ON CONFLICT DO NOTHING} 이라 <b>먼저 넣은 쪽이 값을 정하고</b>
		 * 과거 버킷을 <b>늘리며</b>, 1분 레인({@code minute/canonical_news.py})은 {@code DO UPDATE} 로
		 * {@code available_at = GREATEST(기존, 처리 시각)} 이라 <b>정정이 오면 값을 앞으로 밀어</b>
		 * 과거 버킷을 <b>줄인다</b>. 이 술어는 완결을 증명하지 않고 <b>정직한 하한</b>을 준다 —
		 * 장중 거짓 −100% 는 구조적으로 없애되 지난 날의 값은 여전히 흔들린다.
		 *
		 * <p>⚠️ <b>산출 전부에 건다.</b> 적재 창이 산출마다 다르기 때문이다: {@code o.trig} 은
		 * 시장 SFN 한 번({@code schedule_expression} 기본 15:40)이 통째로 넣는 계단 함수고
		 * (행에 박힌 {@code detected_at}=15:30 은 <b>멱등 키의 결정적 이벤트 시각</b>이지 적재
		 * 시각이 아니다 — {@code steps/load_price_triggers.py}), 뉴스 셋은 하루 종일, 전망은
		 * 06:00 배치, 가격변동 분석은 장중 내내다. 전부에 참인 술어는 "그 날이 다 지났는가"
		 * 하나뿐이라 산출별로 가르지 않는다. 가르려면 산출↔작업 완료 바인딩이 있어야 하고 없다.
		 *
		 * <p>🔴 <b>대가: 그 날의 진짜 결손이 자정까지 조용해진다.</b> 산출이 통째로 0 인 장애를
		 * R13 이 당일에 잡던 경로가 사라진다 — 그리고 그걸 대신 잡을 규칙이 지금 없다(완전성 축이
		 * 대부분 {@code UNKNOWN} 이라 작업이 {@code FULFILLED} 로 끝나면 R05~R07 이 조용하다,
		 * ALPHA-728). 거짓 P1 다섯 중 넷을 없애는 값으로 그 지연을 받았고, 완전성 축이 서면
		 * 그쪽이 당일 검출을 맡는다.
		 *
		 * <p>{@code isBefore} 로 쓴다 — 미래 날짜는 서비스가 400 으로 막지만, 그 가드가 이 판정의
		 * 전제로 <b>여기에 적혀 있지 않으므로</b> 등호로 좁히면 가드가 빠지는 날 조용히 샌다. */
		boolean targetDayIsIncomplete = !day.isBefore(kstToday);
		List<LocalDate> baseDays = baseDays(day, holidays);

		List<LocalDate> queried = new ArrayList<>(baseDays);
		queried.add(day);   // 기준일 후보는 day 미만이라 오늘과 겹치지 않는다
		String placeholders = queried.stream().map(d -> "?").collect(Collectors.joining(","));
		/* `analysisAtRange` 산출이 받는 시각 범위 — 조회한 날짜들의 첫날 00:00 부터 마지막 날 다음
		 * 00:00(KST)까지. 날짜 목록이 그 안에서 다시 거르므로 범위는 넓혀도 값이 안 바뀐다. */
		List<Object> ranged = new ArrayList<>();
		ranged.add(queried.stream().min(Comparator.naturalOrder()).orElseThrow()
				.atStartOfDay(KST).toOffsetDateTime());
		ranged.add(queried.stream().max(Comparator.naturalOrder()).orElseThrow().plusDays(1)
				.atStartOfDay(KST).toOffsetDateTime());
		ranged.addAll(queried);

		return OUTPUTS.stream().map(spec -> {
			Map<LocalDate, Long> byDay = new HashMap<>();
			// 캐스트가 없으면 varargs 오버로드가 ResultSetExtractor 와 겹쳐 모호해진다.
			jdbc.query(spec.sql().formatted(placeholders),
					(RowCallbackHandler) rs -> byDay.put(rs.getObject("d", LocalDate.class),
							rs.getLong("n")),
					spec.analysisAtRange() ? ranged.toArray() : queried.toArray());
			// 결과에 없는 거래일은 0 이다 — 그날 산출이 없었다는 실측이지 모름이 아니다.
			List<Long> base = baseDays.stream().map(d -> byDay.getOrDefault(d, 0L)).toList();
			/* 🔴 휴장일에 장 산출의 기준을 주면 소비자가 −100% 편차로 판정한다. `today` 의 0 은
			 * 실측이 맞지만 **비교할 평소가 없는 날**이라 기준 쪽을 비운다 — 없는 사실을 지어내지
			 * 않고 이미 있는 "기준 없음" 규약을 탄다. 미완결일도 같은 지렛대를 탄다(위 주석) —
			 * 두 사유 모두 "이 날의 값은 비교 대상이 아니다"이지 "표본이 없다"가 아니다. */
			Double median = targetDayIsIncomplete || (spec.marketBound() && targetIsNonMarketDay)
					? null : median(base);
			/* 기준일별 건수를 그대로 싣는다(ALPHA-1289) — 조회는 위 한 번뿐이다. 날짜 오름차순으로
			 * 뒤집고, 조회일은 넣지 않는다(미완결일의 값이 마지막 점으로 서면 매일 급락으로 읽힌다). */
			List<OutputDay> history = baseDays.stream().sorted()
					.map(d -> new OutputDay(d, byDay.getOrDefault(d, 0L))).toList();
			return new OutputRow(spec.id(), spec.label(), spec.unit(),
					byDay.getOrDefault(day, 0L), median, history);
		}).toList();
	}

	/**
	 * 기준을 잴 직전 거래일 — 런이 있던 날과 <b>계획 결손일</b>의 합집합에서 휴장일을 빼고 최근 10개.
	 *
	 * <p>🔴 <b>순서 셋이 다 중요하다.</b> ① 계획 결손일을 합집합에 넣는다 — 안 넣으면 Planner 가
	 * 통째로 실패한 날의 실측 0 이 표본에서 빠진다. ② 휴장일 제외는 <b>합집합 뒤 한 번</b> — 각
	 * 소스 안에서 빼면 다른 소스가 같은 날짜를 되살린다. ③ 자르기는 <b>제외 뒤</b> — 먼저 자르면
	 * 표본이 10개 미만으로 줄고 그만큼의 거래일이 영영 안 들어온다.
	 *
	 * <p>⚠️ "런 0건인 날을 표본에서 빼는 쪽이 과민이라 안전하다"는 <b>거짓이다</b>. 편차 판정은
	 * 양방향이라 기준이 올라가면 <b>위쪽 이상이 조용해진다</b> — 거짓 음성도 같이 만든다. 어느 쪽도
	 * 안전하지 않으므로 더 그럴듯한 쪽을 고른다: 휴장일에는 Planner 가 정상적으로 돌아 런 행이
	 * 생기므로(그래서 휴장 신호도 그때 남는다) <b>런이 0건인 평일 = 거래일</b>일 확률이 압도적이다.
	 *
	 * <p>🔴 <b>남는 불확실은 "장애와 휴장이 겹친 날" 하나다</b> — 평일 휴장인데 Planner 가 통째로
	 * 실패하면 런이 없어 휴장 신호도 없고, 계획 결손일로만 표본에 들어와 <b>거래일로 오인된다</b>
	 * (리뷰가 짚었다). 그 날을 조회하면 장 산출에 기준이 실린다. 계측이 없어 지금은 못 가르고,
	 * 위 확률 판단이 그 대가를 감수한 것이다.
	 *
	 * <p>⚠️ {@code PLANNER_MISSING} 이 주말을 거른다고 읽으면 안 된다 — {@code _due_slots} 는
	 * 레인별 주말 설정을 본다({@link #marketDay} 주석). 주말은 그 술어가 뺀다.
	 */
	private List<LocalDate> baseDays(LocalDate day, List<LocalDate> holidays) {
		LocalDate upTo = day.minusDays(1);
		TreeSet<LocalDate> days = new TreeSet<>(Comparator.reverseOrder());
		days.addAll(jdbc.queryForList(BASE_DAYS_SQL, LocalDate.class, upTo));
		days.addAll(slotDays(upTo));
		/* 장이 안 서는 날을 뺀다 — `holidays` 는 `day` 이하라 여기 후보(`upTo` 이하)를 덮는다.
		 * ⚠️ 자르기는 이 뒤다(위 주석). */
		return days.stream().filter(d -> marketDay(d, holidays)).limit(10).toList();
	}

	/**
	 * 장이 서는 날인가 — <b>물음이 둘로 갈린다</b>.
	 *
	 * <p>🔴 <b>주말은 원장이 답해 주지 않는다.</b> {@code skip_reason='NON_TRADING_DAY'} 는 그날
	 * <b>달력에 매인 레인이 실제로 돌았을 때만</b> 생긴다 — 뉴스 레인은 주 7일 돌고 그 런에도
	 * {@code trading_date} 가 박히므로, 시장 레인이 안 돈 주말은 <b>원장상 평범한 거래일처럼
	 * 보인다</b>. dev 실측(2026-08-11): 주말 {@code trading_date} 4일 중 2일(08-08 토·08-09 일)이
	 * 뉴스 런만 있고 skip 행이 <b>0</b> 이었다. 그대로 두면 그 이틀의 시장 산출 0 이 기준 표본에
	 * 섞이고, 그 날을 조회하면 비어야 할 {@code base} 가 실린다.
	 *
	 * <p>그래서 <b>주말은 달력이, 평일 휴장은 원장의 skip 신호가</b> 답한다. 주말 규칙은
	 * data-pipeline 의 {@code ops/trading_calendar.py}({@code day.weekday() >= 5})와 같은 식이다.
	 *
	 * <p>⚠️ 슬롯 생성({@code ops/entry.py} 의 {@code _due_slots})은 <b>같은 기준이 아니다</b> —
	 * {@code cand.weekday() >= 5 and not weekend} 라 <b>레인별 주말 설정</b>을 본다. 뉴스처럼 주말에
	 * 도는 레인은 주말 슬롯을 만들고, 그 결손 이슈도 주말 날짜로 열린다(dev 원장에 실재:
	 * {@code news:2026-08-08T15:00} 토요일). 그 날짜를 여기서 뺀다.
	 *
	 * <p>⚠️ <b>한 술어를 두 자리가 함께 쓴다</b> — 표본에서 빼는 자리와 "오늘 장이 섰나"를 묻는
	 * 자리. 갈리면 이 파일이 이미 겪은 종류의 결함이 된다.
	 */
	private static boolean marketDay(LocalDate date, List<LocalDate> holidays) {
		return date.getDayOfWeek().getValue() <= 5 && !holidays.contains(date);
	}

	/** 표본이 없으면 <b>null</b> — 기준 없음이지 0 이 아니다. 짝수면 가운데 둘의 평균이다. */
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
				null, null,
				rs.getString("execution_arn"));
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
		/* 런 행이 없으니 실행 ARN 도 없다 — 제어면에 물을 대상 자체가 없다. 계산해 넣지 않는다:
		 * `expected_execution_arn` 은 Planner 가 런 행을 만들 때 함께 남기는 값이고, 여기서
		 * 지어내면 "계획은 있는데 런 행이 없다"는 사실이 "실행이 없다"는 제어면 관측으로 바뀐다. */
		return new RunRow(runKey, lane, slotDate, null, null, null, true, true, null);
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
				nullableLong(rs, "unsupported_records"),
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
