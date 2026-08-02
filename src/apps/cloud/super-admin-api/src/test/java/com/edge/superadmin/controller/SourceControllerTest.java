package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.repository.PipelineStatusRepository.AttemptStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.CompletenessStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.GridCell;
import com.edge.superadmin.repository.PipelineStatusRepository.GridSlot;
import com.edge.superadmin.repository.PipelineStatusRepository.IssueStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.OverviewLane;
import com.edge.superadmin.repository.PipelineStatusRepository.OverviewTask;
import com.edge.superadmin.repository.PipelineStatusRepository.PipelineRunStatus;
import com.edge.superadmin.repository.PipelineStatusRepository.TaskStatus;
import com.edge.superadmin.service.SourceService;
import com.edge.superadmin.support.FakePipelineStatusRepository;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui sources 도메인) 검증 — 원장 4축 어휘가 뭉개지지 않고 그대로 내려오는지가
 * 핵심이다(ALPHA-514, 드릴다운 574).
 */
class SourceControllerTest {

	private static final String RUN_KEY = "etf-daily:2026-07-27T15:40";

	private static final OffsetDateTime STARTED =
			OffsetDateTime.of(2026, 7, 27, 6, 35, 0, 0, ZoneOffset.UTC);
	private static final OffsetDateTime FINISHED =
			OffsetDateTime.of(2026, 7, 27, 6, 40, 0, 0, ZoneOffset.UTC);

	private MockMvc mvc(PipelineRunStatus run) {
		return MockMvcBuilders
				.standaloneSetup(new SourceController(
						new SourceService(new FakePipelineStatusRepository(run))))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	private MockMvc gridMvc(List<GridSlot> slots) {
		return MockMvcBuilders
				.standaloneSetup(new SourceController(
						new SourceService(new FakePipelineStatusRepository(null, slots))))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	private static AttemptStatus attempt(int number, String status, Integer exitCode,
			String failureReason, String recordSource) {
		return new AttemptStatus("att-" + number, number, "arn:aws:ecs:task/" + number, status,
				STARTED, FINISHED, exitCode, failureReason, recordSource);
	}

	/**
	 * 픽스처는 운영에서 실제로 나오는 이종을 섞는다 — 정상 작업·SKIPPED 작업·봉투를 못 낸 작업.
	 * 셋 다 dev 최신 런에 실재한다(21 FULFILLED · 3 SKIPPED · 카운터 전부 NULL). 동종만 넣으면
	 * null 경로를 아예 안 밟아 결함이 초록으로 통과한다.
	 */
	private static PipelineRunStatus sampleRun() {
		return new PipelineRunStatus(RUN_KEY, "LAUNCHED", "FAILED",
				LocalDate.of(2026, 7, 27), List.of(
				// 실패 후 재시도로 성공 — 마지막 한 건만 보면 실패했다는 사실이 사라진다.
				// 원장은 성공한 2번 시도를 현재 결과로 지목한다(current_attempt_id).
				new TaskStatus("raw", "PRICE_COLLECTION_KIS", "price_daily", "DUE",
						"FULFILLED", "VALID", 2736L, 0L, null, null, null, null, FINISHED, null, null,
						List.of(attempt(1, "FAILED", 1, "ecs task exited", "WRAPPER"),
								attempt(2, "SUCCEEDED", 0, null, "RECONCILER_BACKFILL")),
						"att-2"),
				new TaskStatus("raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "SKIPPED",
						null, null, null, null, null, null, null, null, null, "NON_TRADING_DAY", null,
						List.of(), null),
				// 실행은 성공인데 데이터는 불완전 — 두 축이 따로 내려가는지 잠근다.
				new TaskStatus("feature", "TAG_NEWS", "news_assertions", "DUE",
						"FULFILLED", "INCOMPLETE", null, null, null, null, null, null, FINISHED, null,
						null, List.of(attempt(1, "SUCCEEDED", 0, null, "WRAPPER")), "att-1")),
				List.of(new IssueStatus("LEDGER_GAP", "task", "TAG_NEWS", "OPEN", 3,
						STARTED, FINISHED, null)));
	}

	@Test
	void 최신_런의_런헤더와_작업목록을_그대로_낸다() throws Exception {
		mvc(sampleRun()).perform(get("/api/v1/sources/report"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.run.runKey").value(RUN_KEY))
				.andExpect(jsonPath("$.result.run.launchStatus").value("LAUNCHED"))
				// WHY: 런 전체가 FAILED 여도 개별 작업은 FULFILLED 일 수 있다(dev 실측: orch=FAILED
				//      인데 21/25 성공). 두 축이 함께 내려가야 화면이 그 모순을 보여준다 — 작업
				//      목록만 보면 "대체로 초록"인데 런은 실패다.
				.andExpect(jsonPath("$.result.run.orchestrationStatus").value("FAILED"))
				.andExpect(jsonPath("$.result.run.tradingDate").value("2026-07-27"))
				.andExpect(jsonPath("$.result.tasks.length()").value(3))
				.andExpect(jsonPath("$.result.tasks[0].stage").value("raw"))
				.andExpect(jsonPath("$.result.tasks[0].taskKey").value("PRICE_COLLECTION_KIS"))
				.andExpect(jsonPath("$.result.tasks[0].planStatus").value("DUE"))
				.andExpect(jsonPath("$.result.tasks[0].outcome").value("FULFILLED"))
				.andExpect(jsonPath("$.result.tasks[0].recordsOut").value(2736));
	}

	@Test
	void 건수_신호가_없으면_0이_아니라_null_로_내려간다() throws Exception {
		// WHY: 0 으로 메우면 화면에서 "0건 처리"와 "신호 없음"이 구분되지 않는다(ALPHA-182 계약).
		//      결측을 낙관값으로 채우는 것이 이 레포 계측 결함의 일관된 방향이라 명시적으로 잠근다.
		mvc(sampleRun()).perform(get("/api/v1/sources/report"))
				.andExpect(jsonPath("$.result.tasks[2].taskKey").value("TAG_NEWS"))
				.andExpect(jsonPath("$.result.tasks[2].outcome").value("FULFILLED"))
				// 실행 성공 옆의 데이터 결손 — 이 축이 빠지면 불완전한 산출이 온전한 초록이 된다.
				.andExpect(jsonPath("$.result.tasks[2].dataStatus").value("INCOMPLETE"))
				.andExpect(jsonPath("$.result.tasks[2].recordsOut").doesNotExist())
				.andExpect(jsonPath("$.result.tasks[2].failedRecords").doesNotExist())
				// 완전성 미배선 작업은 빈 객체가 아니라 null — 기대값이 있는 UNKNOWN과 구분한다.
				// doesNotExist() 는 키 부재도 통과시켜 이 구분을 못 잠근다. 키는 있고 값이 null
				// 이어야 TS 계약(TaskCompleteness | null)이 undefined 를 받지 않는다.
				.andExpect(jsonPath("$.result.tasks[2].completeness").value(nullValue()));
	}

	@Test
	void ETF_완전성은_행_건수와_섞거나_재계산하지_않고_그대로_내려간다() throws Exception {
		// WHY: recordsOut 은 구성종목 행 수, received 는 unique ETF 수다. API가 둘을 합치거나
		//      expected-received 를 다시 계산하면 원장이 판정한 사실과 다른 다섯 번째 상태가 생긴다.
		PipelineRunStatus run = new PipelineRunStatus(RUN_KEY, "LAUNCHED", "SUCCEEDED", null,
				List.of(
						// missing 을 일부러 33-32 와 다르게 둔다 — 1 이면 재계산하는 API 도
						// 통과해 "원장 값 그대로"라는 이 테스트의 전제를 못 잠근다.
						new TaskStatus("raw", "ETF_HOLDINGS_COLLECTION_KRX", "etf_holdings", "DUE",
								"FULFILLED", "INCOMPLETE", 4120L, 0L,
								new CompletenessStatus(33L, 32L, 3L),
								null, null, null, FINISHED, null, null, List.of(), null),
						// 기대 스냅샷은 있지만 observer가 수신 수를 못 낸 상태. null을 0으로 만들면
						// "33개 모두 누락"이라는 거짓 사실이 된다.
						new TaskStatus("raw", "NAV_COLLECTION_KIS", "etf_nav", "DUE",
								"FULFILLED", "UNKNOWN", null, null,
								new CompletenessStatus(33L, null, null),
								null, null, null, FINISHED, null, null, List.of(), null)),
				List.of());

		mvc(run).perform(get("/api/v1/sources/report"))
				.andExpect(jsonPath("$.result.tasks[0].recordsOut").value(4120))
				.andExpect(jsonPath("$.result.tasks[0].completeness.expected").value(33))
				.andExpect(jsonPath("$.result.tasks[0].completeness.received").value(32))
				.andExpect(jsonPath("$.result.tasks[0].completeness.missing").value(3))
				.andExpect(jsonPath("$.result.tasks[1].completeness.expected").value(33))
				// 미관측은 키를 지우는 게 아니라 명시적 null 이다 — 지우면 UI 가 "누락 0" 과
				// 구분할 근거를 잃는다. doesNotExist() 로는 그 차이를 잠글 수 없다.
				.andExpect(jsonPath("$.result.tasks[1].completeness.received").value(nullValue()))
				.andExpect(jsonPath("$.result.tasks[1].completeness.missing").value(nullValue()));
	}

	@Test
	void 계획에서_빠진_작업은_outcome_없이_SKIPPED_로_내려간다() throws Exception {
		// WHY: SKIPPED(휴장이라 안 함)를 FULFILLED(해서 됐음)와 같은 초록으로 뭉개면 운영자가
		//      "오늘 수집이 돌긴 했나"에 답할 수 없다. plan 축과 outcome 축을 분리해 내린다.
		mvc(sampleRun()).perform(get("/api/v1/sources/report"))
				.andExpect(jsonPath("$.result.tasks[1].planStatus").value("SKIPPED"))
				.andExpect(jsonPath("$.result.tasks[1].outcome").doesNotExist())
				.andExpect(jsonPath("$.result.tasks[1].lastFinishedAt").doesNotExist())
				// 왜 빠졌는지는 이 필드 말고 저장되는 곳이 없다 — 없으면 화면은 "그냥 안 했다"만 안다.
				.andExpect(jsonPath("$.result.tasks[1].skipReason").value("NON_TRADING_DAY"));
	}

	@Test
	void 시도_전량을_내리고_마지막_시도에서_실행상태를_파생한다() throws Exception {
		// WHY: 예전 구현은 SQL 이 마지막 한 건만 남겨, **실패 후 재시도로 성공한 작업**이 화면에서
		//      처음부터 성공한 것과 구분되지 않았다. 사후 복구(RECONCILER_BACKFILL)와 정상 계측도
		//      마찬가지로 뭉개졌다 — 원장이 스스로 메운 행이 관측된 실행처럼 보이는 방향이다.
		mvc(sampleRun()).perform(get("/api/v1/sources/report"))
				.andExpect(jsonPath("$.result.tasks[0].attempts.length()").value(2))
				.andExpect(jsonPath("$.result.tasks[0].attempts[0].executionStatus")
						.value("FAILED"))
				.andExpect(jsonPath("$.result.tasks[0].attempts[0].exitCode").value(1))
				.andExpect(jsonPath("$.result.tasks[0].attempts[0].failureReason")
						.value("ecs task exited"))
				.andExpect(jsonPath("$.result.tasks[0].attempts[1].recordSource")
						.value("RECONCILER_BACKFILL"))
				// 표시용 executionStatus 는 **마지막 원소에서 파생**된다(정의는 한 곳에만 둔다).
				.andExpect(jsonPath("$.result.tasks[0].executionStatus").value("SUCCEEDED"))
				.andExpect(jsonPath("$.result.tasks[0].lastFinishedAt").exists());
	}

	@Test
	void 실행상태는_시각_순서가_아니라_원장이_지목한_시도에서_나온다() throws Exception {
		// WHY: Reconciler 의 사후 복구는 실제 실행 시각을 몰라 started_at 에 **복구 시각**을 넣는다
		//      (ledger.py backfill_attempt). 그래서 뒤늦게 복구된 **옛 실패 시도**가 시각순으로는
		//      맨 뒤에 온다 — 순서로 고르면 이미 성공한 작업이 화면에서 실패로 보인다.
		//      원장의 current_attempt_id 가 그 답을 이미 갖고 있으므로 그걸 따른다.
		PipelineRunStatus run = new PipelineRunStatus(RUN_KEY, "LAUNCHED", "SUCCEEDED", null,
				List.of(new TaskStatus("raw", "NAV_COLLECTION_KIS", "etf_nav", "DUE",
						"FULFILLED", "VALID", 30L, 0L, null, null, null, null, FINISHED, null, null,
						List.of(attempt(1, "SUCCEEDED", 0, null, "WRAPPER"),
								// 시각상 마지막이지만 실제로는 먼저 있었던 실패의 사후 복구다.
								attempt(2, "FAILED", 1, "ecs task exited",
										"RECONCILER_BACKFILL")),
						"att-1")),
				List.of());

		mvc(run).perform(get("/api/v1/sources/report"))
				.andExpect(jsonPath("$.result.tasks[0].executionStatus").value("SUCCEEDED"))
				// 이력 자체는 순서대로 전량 남는다 — 고르는 기준만 다르다.
				.andExpect(jsonPath("$.result.tasks[0].attempts.length()").value(2))
				.andExpect(jsonPath("$.result.tasks[0].attempts[1].executionStatus")
						.value("FAILED"));
	}

	@Test
	void 대조_이슈를_런과_함께_내린다() throws Exception {
		// WHY: 원장은 이슈를 판정해 저장하는데 콘솔은 그동안 한 건도 보여주지 않았다 — 화면에
		//      없으면 운영자에게는 없는 사실이다(dev 의 거짓 LEDGER_GAP 17건이 그렇게 묻혀 있었다).
		mvc(sampleRun()).perform(get("/api/v1/sources/report"))
				.andExpect(jsonPath("$.result.issues.length()").value(1))
				.andExpect(jsonPath("$.result.issues[0].issueType").value("LEDGER_GAP"))
				.andExpect(jsonPath("$.result.issues[0].status").value("OPEN"))
				.andExpect(jsonPath("$.result.issues[0].occurrenceCount").value(3))
				// 내부 ID 가 아니라 운영자가 아는 작업 이름으로 붙는다.
				.andExpect(jsonPath("$.result.issues[0].taskKey").value("TAG_NEWS"));
	}

	@Test
	void 런_키를_주면_그_런을_낸다() throws Exception {
		mvc(sampleRun()).perform(get("/api/v1/sources/report").param("runKey", RUN_KEY))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.run.runKey").value(RUN_KEY));
	}

	@Test
	void 없는_런_키는_빈_리포트가_아니라_404_다() throws Exception {
		// WHY: 빈 리포트로 답하면 오타 친 런 키가 "원장이 비어 있다"로 보인다 — 운영자가 없는
		//      사실을 있는 것처럼 읽는다. 두 상태는 다른 사실이라 다른 응답이어야 한다.
		mvc(sampleRun()).perform(get("/api/v1/sources/report").param("runKey", "etf-daily:없는런"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.isSuccess").value(false))
				.andExpect(jsonPath("$.code").value("ADMN4041"));
	}

	@Test
	void 격자는_슬롯_배열_순서와_셀의_모든_축을_그대로_낸다() throws Exception {
		// 픽스처는 이종을 섞는다 — 정상 슬롯·기동 실패 슬롯(작업 0개), 셀도 정상·SKIPPED·건수
		// 결측·실행 중(PENDING+running).
		List<GridSlot> slots = List.of(
				new GridSlot("etf-daily:2026-07-26T15:40", "LAUNCHED", "SUCCEEDED",
						LocalDate.of(2026, 7, 26), List.of(
						new GridCell("raw", "PRICE_COLLECTION_KIS", "DUE", "FULFILLED", "VALID",
								2736L, 0L, null, null, false),
						new GridCell("raw", "NEWS_COLLECTION_BIGKINDS", "SKIPPED", null, null,
								null, null, "NON_TRADING_DAY", null, false),
						new GridCell("feature", "TAG_NEWS", "DUE", "FULFILLED", "INCOMPLETE",
								null, null, null, null, false),
						// WHY: outcome 은 wrapper 가 끝날 때 쓴다 — 실행 중엔 PENDING 인 채로
						//      running 만 참이라, 이 축이 안 내려가면 런이 도는 내내 진행 중
						//      작업이 "아직 시작도 안 함"과 같은 셀로 보인다(#297 P2 와 동형).
						new GridCell("feature", "ASSEMBLE_EVENTS", "DUE", "PENDING", "UNKNOWN",
								null, null, null, null, true))),
				// WHY: 기동 실패는 orchestration 이 영영 null 이고 기대 작업도 없다 — 이 슬롯을
				//      열에서 빼면 "아예 못 뜬 런"이 격자에서 사라진다(부재가 1급 신호인 화면).
				new GridSlot("etf-daily:2026-07-27T15:40", "LAUNCH_FAILED", null, null, List.of()));

		gridMvc(slots).perform(get("/api/v1/sources/grid"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.days").value(30))   // 기본 창 30일
				.andExpect(jsonPath("$.result.slots.length()").value(2))
				.andExpect(jsonPath("$.result.slots[0].runKey").value("etf-daily:2026-07-26T15:40"))
				.andExpect(jsonPath("$.result.slots[0].tradingDate").value("2026-07-26"))
				.andExpect(jsonPath("$.result.slots[0].tasks.length()").value(4))
				.andExpect(jsonPath("$.result.slots[0].tasks[0].outcome").value("FULFILLED"))
				.andExpect(jsonPath("$.result.slots[0].tasks[0].recordsOut").value(2736))
				.andExpect(jsonPath("$.result.slots[0].tasks[0].running").value(false))
				// plan 축과 outcome 축은 격자에서도 합쳐지지 않는다.
				.andExpect(jsonPath("$.result.slots[0].tasks[1].planStatus").value("SKIPPED"))
				.andExpect(jsonPath("$.result.slots[0].tasks[1].outcome").doesNotExist())
				.andExpect(jsonPath("$.result.slots[0].tasks[1].skipReason")
						.value("NON_TRADING_DAY"))
				// 건수 결측은 0 이 아니라 부재다(ALPHA-182) — 격자 경로에서도 같은 계약.
				.andExpect(jsonPath("$.result.slots[0].tasks[2].recordsOut").doesNotExist())
				.andExpect(jsonPath("$.result.slots[0].tasks[2].dataStatus").value("INCOMPLETE"))
				// 실행 중 축 — PENDING 과 running 이 함께 내려간다.
				.andExpect(jsonPath("$.result.slots[0].tasks[3].outcome").value("PENDING"))
				.andExpect(jsonPath("$.result.slots[0].tasks[3].running").value(true))
				.andExpect(jsonPath("$.result.slots[1].launchStatus").value("LAUNCH_FAILED"))
				.andExpect(jsonPath("$.result.slots[1].orchestrationStatus").doesNotExist())
				.andExpect(jsonPath("$.result.slots[1].tasks.length()").value(0));
	}

	@Test
	void 격자_창_범위_밖의_days_는_400_이다() throws Exception {
		// WHY: days<1 은 창이 미래로 뒤집혀 **조용히 빈 격자**가 된다 — 운영자가 "원장이 비었다"로
		//      오독하는 화면이라, 잘못된 요청은 빈 데이터가 아니라 에러로 답한다.
		gridMvc(List.of()).perform(get("/api/v1/sources/grid").param("days", "0"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.isSuccess").value(false))
				.andExpect(jsonPath("$.code").value("ADMN4001"));
		gridMvc(List.of()).perform(get("/api/v1/sources/grid").param("days", "367"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
	}

	@Test
	void 창_안에_런이_없으면_에러가_아니라_빈_격자다() throws Exception {
		gridMvc(List.of()).perform(get("/api/v1/sources/grid").param("days", "7"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.days").value(7))
				.andExpect(jsonPath("$.result.slots.length()").value(0));
	}

	@Test
	void 원장에_런이_없으면_에러가_아니라_빈_리포트다() throws Exception {
		// WHY: 초기 환경·원장 미가동은 장애가 아니다. 여기서 500 을 내면 콘솔 페이지가 통째로
		//      안 뜬다 — 볼 게 없는 것과 고장 난 것은 다르다.
		mvc(null).perform(get("/api/v1/sources/report"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.run").doesNotExist())
				.andExpect(jsonPath("$.result.tasks.length()").value(0))
				.andExpect(jsonPath("$.result.issues.length()").value(0));
	}

	/* ---------- Run Overview (ALPHA-683) ---------- */

	private MockMvc overviewMvc(List<OverviewLane> lanes) {
		return MockMvcBuilders
				.standaloneSetup(new SourceController(
						new SourceService(new FakePipelineStatusRepository(null, List.of(), lanes))))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	private static final OffsetDateTime PAST = OffsetDateTime.now(ZoneOffset.UTC).minusHours(2);
	private static final OffsetDateTime PLANNED = OffsetDateTime.now(ZoneOffset.UTC);
	private static final OffsetDateTime FUTURE = OffsetDateTime.now(ZoneOffset.UTC).plusHours(2);

	private static OverviewTask task(String taskKey, String outcome, String dataStatus,
			boolean required, OffsetDateTime deadline) {
		return new OverviewTask("raw", taskKey, "DUE", outcome, dataStatus, required,
				deadline, null, null, null, null);
	}

	@Test
	void 정상_SKIPPED_와_데이터_UNKNOWN_은_READY_를_깨지_않는다() throws Exception {
		// WHY: 완전성 미배선 작업은 설계상 UNKNOWN 이고 비거래일 SKIPPED 는 "할 일이 아니었다"다 —
		//      이 둘을 결함으로 세면 첫 화면이 상시 DEGRADED 라, 진짜 결함이 늑대소년이 된다(스펙 §7).
		List<OverviewLane> lanes = List.of(new OverviewLane("etf-daily", RUN_KEY, "LAUNCHED",
				"SUCCEEDED", LocalDate.of(2026, 7, 27), PLANNED, List.of(
				task("PRICE_COLLECTION_KIS", "FULFILLED", "UNKNOWN", true, PAST),
				new OverviewTask("raw", "NEWS_COLLECTION_BIGKINDS", "SKIPPED", null, null,
						true, null, null, null, null, null))));

		overviewMvc(lanes).perform(get("/api/v1/sources/overview"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("READY"))
				.andExpect(jsonPath("$.result.lanes[0].counts.due").value(1))
				.andExpect(jsonPath("$.result.lanes[0].counts.skipped").value(1))
				.andExpect(jsonPath("$.result.lanes[0].defects.length()").value(0));
	}

	@Test
	void 필수_작업의_데이터_결손은_DEGRADED_이고_결함이_단계_순으로_온다() throws Exception {
		// WHY: "실행 성공 ≠ 데이터 유효" — FULFILLED+INCOMPLETE 조합이 이 화면의 존재 이유다.
		//      결함 목록은 단계 순 — 앞 단계(raw) 결함이 뒤 단계(feature)보다 먼저다. 같은 단계
		//      안 순서는 실행 순서가 아니라서 화면도 그 이상을 주장하지 않는다(봇 P2).
		List<OverviewLane> lanes = List.of(new OverviewLane("etf-daily", RUN_KEY, "LAUNCHED",
				"SUCCEEDED", LocalDate.of(2026, 7, 27), PLANNED, List.of(
				new OverviewTask("raw", "ETF_HOLDINGS_COLLECTION_KRX", "DUE", "FULFILLED",
						"INCOMPLETE", true, PAST, null, null, null, null),
				new OverviewTask("feature", "LOAD_ETF_HOLDINGS", "DUE", "FAILED", null,
						true, PAST, null, null, null, null))));

		overviewMvc(lanes).perform(get("/api/v1/sources/overview"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("DEGRADED"))
				.andExpect(jsonPath("$.result.lanes[0].defects.length()").value(2))
				.andExpect(jsonPath("$.result.lanes[0].defects[0].taskKey")
						.value("ETF_HOLDINGS_COLLECTION_KRX"))
				.andExpect(jsonPath("$.result.lanes[0].defects[0].dataStatus").value("INCOMPLETE"))
				.andExpect(jsonPath("$.result.lanes[0].counts.failed").value(1));
	}

	@Test
	void 마감_전_미귀결은_IN_PROGRESS_마감_경과_미귀결은_DEGRADED_다() throws Exception {
		// WHY: PENDING 은 "아직 모른다"인데 마감 전이면 기다림이고 마감 후면 지연이다 —
		//      스펙 §3.3 의 BREACHED 축을 접은 최소 구현. 시계는 서버가 판정한다.
		List<OverviewLane> waiting = List.of(new OverviewLane("etf-daily", RUN_KEY, "LAUNCHED",
				"RUNNING", null, PLANNED, List.of(task("PRICE_COLLECTION_KIS", "PENDING", null, true, FUTURE))));
		overviewMvc(waiting).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("IN_PROGRESS"));

		List<OverviewLane> overdue = List.of(new OverviewLane("etf-daily", RUN_KEY, "LAUNCHED",
				"SUCCEEDED", null, PLANNED, List.of(task("PRICE_COLLECTION_KIS", "PENDING", null, true, PAST))));
		overviewMvc(overdue).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("DEGRADED"))
				.andExpect(jsonPath("$.result.lanes[0].defects[0].overdue").value(true));
	}

	@Test
	void 기동_실패는_마감_전이어도_진행_중이_아니라_BLOCKED_다() throws Exception {
		// WHY: LAUNCH_FAILED 런의 작업은 deadline 전 PENDING 이라 스펙 순서대로면 IN_PROGRESS 가
		//      되는데, 아무것도 돌지 않는 런을 "진행 중"으로 내면 원장이 관대해지는 방향이다.
		List<OverviewLane> lanes = List.of(new OverviewLane("etf-daily", RUN_KEY, "LAUNCH_FAILED",
				null, null, PLANNED, List.of(task("PRICE_COLLECTION_KIS", "PENDING", null, true, FUTURE))));

		overviewMvc(lanes).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("BLOCKED"));
	}

	@Test
	void 기동_충돌도_BLOCKED_다() throws Exception {
		// WHY: LAUNCH_CONFLICT 는 이 런이 뜨지 못했다는 확정 사실인데 어느 분기에도 안 걸리면
		//      마감 전엔 IN_PROGRESS 로 — 안 도는 런이 "진행 중"으로 — 관대해진다(리뷰 1라운드).
		List<OverviewLane> lanes = List.of(new OverviewLane("etf-daily", RUN_KEY,
				"LAUNCH_CONFLICT", null, null, PLANNED,
				List.of(task("PRICE_COLLECTION_KIS", "PENDING", null, true, FUTURE))));

		overviewMvc(lanes).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("BLOCKED"));
	}

	@Test
	void 기동_불명은_확정이_아니라서_마감_전엔_진행_중이다() throws Exception {
		// WHY: 스펙 §7 순서(IN_PROGRESS→UNKNOWN). LAUNCH_UNKNOWN 은 reconciliation 으로 해소될
		//      수 있는 미확정이라 기동 실패처럼 앞당기지 않는다 — 마감이 지나서야 UNKNOWN 이다.
		List<OverviewLane> before = List.of(new OverviewLane("etf-daily", RUN_KEY,
				"LAUNCH_UNKNOWN", null, null, PLANNED,
				List.of(task("PRICE_COLLECTION_KIS", "PENDING", null, true, FUTURE))));
		overviewMvc(before).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("IN_PROGRESS"));

		List<OverviewLane> after = List.of(new OverviewLane("etf-daily", RUN_KEY,
				"LAUNCH_UNKNOWN", null, null, PLANNED,
				List.of(task("PRICE_COLLECTION_KIS", "PENDING", null, true, PAST))));
		overviewMvc(after).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("UNKNOWN"));
	}

	@Test
	void 작업이_하나도_안_적힌_런은_READY_가_아니라_UNKNOWN_이다() throws Exception {
		// WHY: 빈 결함 목록 = 정상이 아니다 — 계획 증거가 없으면 제공 가능 범위를 계산할 수
		//      없다(스펙 §7 UNKNOWN). READY 로 내면 Planner 가 기대 작업을 못 쓴 날이 정상으로
		//      보인다(리뷰 1라운드 — 거짓 정상).
		List<OverviewLane> lanes = List.of(new OverviewLane("etf-daily", RUN_KEY, "LAUNCHED",
				"SUCCEEDED", null, PLANNED, List.of()));

		overviewMvc(lanes).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("UNKNOWN"));
	}

	@Test
	void 마감_없는_미귀결은_영구_진행_중이_아니라_UNKNOWN_이다() throws Exception {
		// WHY: deadline 이 NULL 이면 경과 판정 자체가 불가하다 — "진행 중"으로 내면 실행이 다
		//      끝난 런이 영구히 진행 중으로 남는다(리뷰 1라운드). DUE 인데 귀결 NULL 인 원장
		//      이상도 같은 경로로 접힌다(실패에도 대기에도 안 세면 거짓 정상으로 사라진다).
		List<OverviewLane> lanes = List.of(new OverviewLane("etf-daily", RUN_KEY, "LAUNCHED",
				"SUCCEEDED", null, PLANNED, List.of(
				task("PRICE_COLLECTION_KIS", "PENDING", null, true, null),
				task("NORMALIZE_PRICE", null, null, true, null))));

		overviewMvc(lanes).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("UNKNOWN"))
				.andExpect(jsonPath("$.result.lanes[0].counts.pending").value(2));
	}

	@Test
	void 실행_전체가_실패한_런은_마감_전이어도_진행_중이나_READY_가_아니다() throws Exception {
		// WHY: SFN 이 terminal 실패하면 남은 PENDING 은 저절로 진행되지 않는다 — 마감 전이라
		//      IN_PROGRESS, 결함 목록이 비었다고 READY 로 내면 실패 런이 정상으로 보인다
		//      (리뷰 3라운드 — 헤더의 "실행 전체 FAILED" 옆에 "정상"이 뜨는 모순).
		List<OverviewLane> pendingLeft = List.of(new OverviewLane("etf-daily", RUN_KEY,
				"LAUNCHED", "FAILED", null, PLANNED,
				List.of(task("PRICE_COLLECTION_KIS", "PENDING", null, true, FUTURE))));
		overviewMvc(pendingLeft).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("DEGRADED"));

		List<OverviewLane> noDefects = List.of(new OverviewLane("etf-daily", RUN_KEY,
				"LAUNCHED", "TIMED_OUT", null, PLANNED,
				List.of(task("PRICE_COLLECTION_KIS", "FULFILLED", "UNKNOWN", true, PAST))));
		overviewMvc(noDefects).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].opsStatus").value("DEGRADED"));
	}

	@Test
	void 오늘_런이_아니면_notToday_로_명시된다() throws Exception {
		// WHY: Planner 가 오늘 안 돌면 조회는 어제 런을 최신으로 재사용한다 — "오늘 운영 현황"이
		//      그 사실을 숨기면 지난 READY 가 오늘 정상으로 보인다(리뷰 3라운드). 판정은 서버 시계.
		List<OverviewLane> lanes = List.of(new OverviewLane("news", "news:old", "LAUNCHED",
				"SUCCEEDED", null, PLANNED.minusDays(2),
				List.of(task("NEWS_COLLECTION_BIGKINDS", "FULFILLED", "UNKNOWN", true, PAST))));

		overviewMvc(lanes).perform(get("/api/v1/sources/overview"))
				.andExpect(jsonPath("$.result.lanes[0].notToday").value(true))
				.andExpect(jsonPath("$.result.lanes[0].plannedAt").isNotEmpty());
	}

	@Test
	void 원장에_런이_없으면_빈_레인_목록이다() throws Exception {
		overviewMvc(List.of()).perform(get("/api/v1/sources/overview"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.lanes.length()").value(0));
	}
}
