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
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.stream.Collectors;

/**
 * {@link ConsoleFactsRepository} 의 JdbcTemplate 구현(ALPHA-738).
 *
 * <p>축이 전부 찼다 — <b>조회 창 + 런 축(계획 결손 슬롯 포함) + 작업 축 + 산출 축 + 경계 축</b>.
 * 와이어의 데이터셋 축은 여기서 안 낸다 — 작업의 계약·신선도 컬럼을
 * 재료로 {@code ConsoleFactsService} 가 접는다.
 *
 * <p>날짜 축은 <b>거래일</b>({@code trading_date})이되 {@link #RUN_DAY} 한 식으로만 묻는다.
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
	 * <p>{@code marketBound} 는 <b>이 산출이 장이 서야 나오는가</b>다. 휴장일에는 0 이 실측이 아니라
	 * "그날 나올 것이 아니었다"인데 응답에는 그 둘을 가르는 자리가 없다({@code today} 는 수 하나다).
	 * 그래서 그날은 <b>기준을 안 준다</b> — 소비자는 기준 없는 산출을 편차 판정에서 뺀다. 뉴스 갈래는
	 * 휴장일에도 도니까 해당 없다.
	 */
	private record OutputSpec(String id, String label, String unit, boolean marketBound,
			String sql) {
	}

	/**
	 * 날짜 축이 <b>둘</b>이다 — 거래일 컬럼을 가진 산출({@code trade_date})과 수집 시각뿐인 산출
	 * ({@code available_at} 의 KST 날짜). 뒤 셋은 표현식 필터라 인덱스를 못 탄다
	 * ({@link JdbcNewsLineageRepository} 가 같은 이유로 이미 감수한 비용) — 콘솔 단발 조회 규모라
	 * 지금은 감당 범위지만, 여기는 누적 테이블 <b>셋</b>을 한 요청에서 훑는다. 느려지면 함수 인덱스가
	 * 먼저 붙을 자리다.
	 */
	private static final List<OutputSpec> OUTPUTS = List.of(
			new OutputSpec("o.pub", "게시 ETF", "종", true, """
					SELECT trade_date AS d, count(DISTINCT etf_instrument_id) AS n
					  FROM explanation_result
					 WHERE publication_status = 'PUBLISHED' AND trade_date IN (%s)
					 GROUP BY trade_date"""),
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
	 * <p>⚠️ <b>{@code delivery_type} 을 따로 거르지 않는다.</b> 발번은 2형상이고
	 * (NEW·INVALIDATION — CORRECTION 은 <a href="../../../../../../../../docs/adr/0044-correction-abolition.md">ADR-0044</a>
	 * 로 폐지), {@code ck_tenant_delivery_payload} 가 {@code explanation_result_id} 를
	 * <b>NEW 에만</b> 허용한다 — INVALIDATION 은 그 자리가 NULL 이라 아래 두 조건에 애초에 안 걸린다.
	 * 유형 술어를 덧붙이면 <b>어떤 행도 못 거르는 가드</b>가 하나 늘고 그건 테스트로 겨눌 수도 없다
	 * (같은 이유로 {@code V202608011200} 이 자기참조 금지 제약을 "죽은 제약"이라며 지웠다).
	 * 3형상이 돌아오면 이 두 조건에 유형 술어가 <b>같이</b> 필요하다.
	 *
	 * <p>🔴 <b>무효화 통지가 간 발번은 비게시로 안 센다.</b> 운영자 무효화
	 * ({@link JdbcAnalysisWriteRepository#invalidate})는 결과를 WITHDRAWN 으로 전이하고 그 NEW 를
	 * 받은 테넌트에 INVALIDATION 을 발번하되 <b>원래 NEW 행은 남긴다</b>. 상태만 보면 정상 무효화한
	 * 분석이 전부 "발번했는데 현재 비게시"로 남아 그 수가 <b>영구히 단조 증가</b>한다. 실제 정합
	 * 위반은 "비게시인데 <b>무효화 통지도 안 갔다</b>"이고, 상관은 결과만이 아니라 <b>테넌트까지</b>다
	 * — 무효화는 그 NEW 를 받은 테넌트에만 나가므로({@code invalidate} 의 {@code EXISTS} 제한),
	 * 받고도 통지 못 받은 테넌트가 있으면 그건 진짜 위반이다.
	 *
	 * <p>🔴 <b>테넌트가 하나도 없으면 미발번은 위반이 아니다.</b> {@code _fanout_new}
	 * ({@code analysis-engine} 의 {@code eventstore.py})는 {@code tenant} <b>전건</b>에 발번하므로
	 * 테넌트가 0명이면 0행을 넣고, 호출부는 그걸 {@code fanout_tenants: 0} 인 <b>정상 성공</b>으로
	 * 기록한다. 그 상태를 안 막으면 새 환경 부트스트랩에서 게시본 전건이 위반으로 선다 —
	 * 정상 무효화를 위반으로 세면 안 되는 것과 같은 부류다(리뷰가 잡았다).
	 * ⚠️ dev 실측(2026-08-11)으로는 테넌트 1명이라 이 값이 <b>0</b> 이었다 — 잠복이지 발화가 아니다.
	 *
	 * <p>⏭ <b>남는 것</b>: 게시 <b>뒤</b>에 테넌트가 생기면 그 과거 게시본은 발번이 백필되지 않아
	 * 계속 잡힌다. 가르려면 {@code tenant.created_at} 과 결과 시각을 상관시켜야 하는데 그게 계약인지
	 * 정해진 바 없어 여기서 지어내지 않는다.
	 *
	 * <p>⚠️ <b>무효화 통지의 cursor 순서는 안 본다.</b> {@code INVALIDATION} 을 넣는 경로가
	 * {@link JdbcAnalysisWriteRepository#invalidate} <b>하나뿐</b>이고, 그 문장이 그 결과의 NEW 를
	 * <b>이미 받은</b> 테넌트에만({@code EXISTS} 제한) {@code MAX(cursor)+1} 로 넣어 언제나 NEW 보다
	 * 뒤다. 그래서 {@code inv.cursor > d.cursor} 를 더하면 <b>어떤 행도 못 거르는 가드</b>가 하나
	 * 는다 — {@code delivery_type} 을 안 거르는 것과 같은 판단이다.
	 *
	 * <p>⚠️ 이 표의 writer 가 <b>하나라는 뜻은 아니다</b>(그렇게 적었다가 정정했다): {@code NEW} 는
	 * analysis-engine 의 {@code _fanout_new} 가, {@code INVALIDATION} 은 여기 super-admin-api 가
	 * 넣는다. 순서를 지키는 것은 writer 수가 아니라 <b>무효화 쪽 문장의 {@code EXISTS}</b> 다.
	 *
	 * <p>⚠️ <b>날짜 창을 안 탄다</b> — 인터페이스 {@link BoundaryRow} 주석의 이유. 이 수들의 기준
	 * 시각은 {@code meta.today} 가 아니라 <b>{@code meta.db}</b>(DB 시계)다.
	 */
	private static final String BOUNDARY_SQL = """
			SELECT (SELECT count(*) FROM explanation_result r
			         WHERE r.publication_status = 'PUBLISHED'
			           AND EXISTS (SELECT 1 FROM tenant)
			           AND NOT EXISTS (SELECT 1 FROM tenant_delivery d
			                            WHERE d.explanation_result_id = r.explanation_result_id))
			         AS published_without_delivery,
			       (SELECT count(*) FROM tenant_delivery d
			          JOIN explanation_result r
			            ON r.explanation_result_id = d.explanation_result_id
			         WHERE r.publication_status <> 'PUBLISHED'
			           AND NOT EXISTS (SELECT 1 FROM tenant_delivery inv
			                            WHERE inv.tenant_id = d.tenant_id
			                              AND inv.target_explanation_result_id
			                                  = d.explanation_result_id)) AS delivery_now_nonpublished,
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
				jdbc.query(TASKS_SQL, JdbcConsoleFactsRepository::mapTask, day),
				outputs(day, meta.kstToday()),
				jdbc.queryForObject(BOUNDARY_SQL, (rs, i) -> new BoundaryRow(
						rs.getLong("published_without_delivery"),
						rs.getLong("delivery_now_nonpublished"),
						rs.getLong("delivery_rows"))));
	}

	/**
	 * 산출별 <b>그 날의 값과 직전 거래일 중앙값</b>. 산출마다 조회 한 번이고, 오늘과 기준일을
	 * 한 문장으로 센다.
	 *
	 * <p>⚠️ <b>일관성을 주는 것은 이 문장 구성이 아니라 트랜잭션이다.</b> {@link #facts} 전체가
	 * REPEATABLE READ 라 나눠 세도 같은 스냅샷을 읽는다 — 산출 다섯이 서로 다른 문장인데도
	 * 서로 일관된 것이 그 증거다. 한 문장으로 세는 이유는 왕복을 줄이는 것뿐이고, <b>격리수준을
	 * 낮추면</b> 그때야 나눠 센 값들이 갈린다.
	 */
	private List<OutputRow> outputs(LocalDate day, LocalDate kstToday) {
		/* 휴장일 목록을 한 번만 읽어 두 자리가 같은 술어를 쓰게 한다 — 표본에서 빼는 자리와
		 * "오늘이 휴장인가"를 묻는 자리가 갈리면 이 파일이 이미 겪은 종류의 결함이 된다. */
		List<LocalDate> holidays = jdbc.queryForList(HOLIDAY_DAYS_SQL, LocalDate.class, day);
		boolean targetIsNonMarketDay = !marketDay(day, holidays);
		/* 🔴 <b>그 날의 적재 창이 아직 안 지났으면 비교 대상이 아니다</b>(ALPHA-946). 기준일 후보는
		 * {@code day} 미만이라 <b>적재 창이 지난</b> 하루의 값인데, {@code day} 가 KST 오늘이면 그
		 * 값은 아직 쌓이는 중이다 — 같은 축이 아닌 둘을 나눠 임계에 건다. dev 실측(08-11 14:27 KST)
		 * 에서 산출 다섯 중 <b>넷</b>이 그 이유로 거짓 P1 이었다({@code o.trig} −100%·{@code o.pub}
		 * −71%·{@code o.asr} −29%·{@code o.evt} −59%). 미래 날짜를 400 으로 막는
		 * {@code ConsoleFactsService.parseDateParam} 이 같은 논거를 이미 쓴다 — 오늘은 그
		 * "아직"이 <b>부분</b>으로 오는 날이다.
		 *
		 * <p>⚠️ <b>"완결"이 아니라 "창이 지났다"이다.</b> 지난 날의 값도 소급으로 자란다:
		 * {@code o.pub} 은 {@code trade_date} 가 적재와 분리돼 며칠 뒤에도 늘고, 뉴스 셋의
		 * {@code available_at} 은 writer 마다 뜻이 달라 과거 버킷에 실릴 수 있다
		 * ({@code minute/canonical_news.py} 는 처리 시각이지만 {@code steps/assemble_events.py} 는
		 * <b>{@code published_at}</b>, {@code steps/load_documents.py} 는 {@code fetched_at} 폴백
		 * {@code published_at} 이고 셋 다 {@code ON CONFLICT DO NOTHING} 이라 <b>먼저 넣은 writer 가
		 * 값을 정한다</b>). 이 술어는 완결을 증명하지 않고 <b>정직한 하한</b>을 준다 — 장중 거짓
		 * −100% 는 구조적으로 없애되 소급 적재분은 여전히 낮게 잰다.
		 *
		 * <p>⚠️ <b>다섯 전부에 건다.</b> 적재 창이 산출마다 다르기 때문이다: {@code o.trig} 은
		 * 시장 SFN 한 번({@code schedule_expression} 기본 15:40)이 통째로 넣는 계단 함수고
		 * (행에 박힌 {@code detected_at}=15:30 은 <b>멱등 키의 결정적 이벤트 시각</b>이지 적재
		 * 시각이 아니다 — {@code steps/load_price_triggers.py}), 뉴스 셋은 하루 종일, {@code o.pub}
		 * 은 상주 소비자라 날짜 경계를 넘는다. 다섯 전부에 참인 술어는 "그 날이 다 지났는가"
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

		return OUTPUTS.stream().map(spec -> {
			Map<LocalDate, Long> byDay = new HashMap<>();
			// 캐스트가 없으면 varargs 오버로드가 ResultSetExtractor 와 겹쳐 모호해진다.
			jdbc.query(spec.sql().formatted(placeholders),
					(RowCallbackHandler) rs -> byDay.put(rs.getObject("d", LocalDate.class),
							rs.getLong("n")),
					queried.toArray());
			// 결과에 없는 거래일은 0 이다 — 그날 산출이 없었다는 실측이지 모름이 아니다.
			List<Long> base = baseDays.stream().map(d -> byDay.getOrDefault(d, 0L)).toList();
			/* 🔴 휴장일에 장 산출의 기준을 주면 소비자가 −100% 편차로 판정한다. `today` 의 0 은
			 * 실측이 맞지만 **비교할 평소가 없는 날**이라 기준 쪽을 비운다 — 없는 사실을 지어내지
			 * 않고 이미 있는 "기준 없음" 규약을 탄다. 미완결일도 같은 지렛대를 탄다(위 주석) —
			 * 두 사유 모두 "이 날의 값은 비교 대상이 아니다"이지 "표본이 없다"가 아니다. */
			Double median = targetDayIsIncomplete || (spec.marketBound() && targetIsNonMarketDay)
					? null : median(base);
			return new OutputRow(spec.id(), spec.label(), spec.unit(),
					byDay.getOrDefault(day, 0L), median);
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
