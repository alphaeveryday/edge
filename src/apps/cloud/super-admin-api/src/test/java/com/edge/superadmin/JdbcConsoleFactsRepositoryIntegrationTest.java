package com.edge.superadmin;

import com.edge.superadmin.repository.ConsoleFactsRepository;
import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 콘솔 사실 조회의 <b>조회 창 + 런 축</b> 통합 테스트 — 실 스키마(Testcontainers + Flyway
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

	@Autowired
	private ConsoleFactsRepository repository;

	@Autowired
	private JdbcTemplate jdbc;

	private void insertRun(String id, String runKey, String orchestration, String tradingDate,
			String createdAt, String deadline) {
		insertRun(id, runKey, "etf-daily", orchestration, tradingDate, createdAt, createdAt,
				deadline);
	}

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
		insertRun("r-" + tradingDate, "etf-daily:" + tradingDate + "T15:40", "SUCCEEDED",
				tradingDate, tradingDate + "T06:40:00Z", null);
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
		insertRun("r-b", "news:2026-08-03T09:00", "news", "RUNNING", null,
				"2026-08-03T00:00:00Z", "2026-08-03T00:10:00Z", null);
		insertRun("r-a", "etf-daily:2026-08-03T15:40", "etf-daily", "SUCCEEDED", "2026-08-03",
				"2026-08-03T06:40:00Z", "2026-08-03T07:20:34Z", "2026-08-03T08:00:00Z");
		insertTradingDay("2026-08-01");

		assertThat(repository.facts(DAY).runs()).containsExactly(
				new RunRow("etf-daily:2026-08-03T15:40", "etf-daily", DAY, "SUCCEEDED",
						OffsetDateTime.parse("2026-08-03T07:20:34Z"),
						OffsetDateTime.parse("2026-08-03T08:00:00Z")),
				new RunRow("news:2026-08-03T09:00", "news", null, "RUNNING",
						OffsetDateTime.parse("2026-08-03T00:10:00Z"), null));
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

	/** 원장이 비면 DB 시계의 KST 오늘 — 날짜가 없으면 화면이 "무엇을 본 응답인가"를 못 말한다. */
	@Test
	void 원장이_비면_DB_시계의_KST_오늘을_본다() {
		assertThat(repository.facts(null).today())
				.isEqualTo(jdbc.queryForObject(
						"SELECT (now() AT TIME ZONE 'Asia/Seoul')::date", LocalDate.class));
	}
}
