package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.repository.ConsoleFactsRepository.BoundaryRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.ConsoleFacts;
import com.edge.superadmin.repository.ConsoleFactsRepository.OutputRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.TaskRow;
import com.edge.superadmin.service.ConsoleFactsService;
import com.edge.superadmin.support.FakeConsoleFactsRepository;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 콘솔 사실 응답의 <b>부재 규약</b>과 데이터셋 파생 검증(ALPHA-738).
 *
 * <p>이 계약의 핵심은 값이 아니라 <b>부재의 종류</b>다: 실측 0 은 {@code 0}, 집계 없음은
 * {@code null}, 계측 없음은 <b>키 자체가 없다</b>. 셋이 뭉개지면 화면이 계측 공백을 "봤고
 * 괜찮다"로 그리고, 그게 이 콘솔이 없애려는 칸 혼동이다. 그래서 JSON 문자열의 키 유무까지 본다 —
 * 자바 타입 단언으로는 Jackson 설정 한 줄이 바꿔 놓는 것을 못 잡는다.
 */
class ConsoleControllerTest {

	private static final OffsetDateTime DB_NOW =
			OffsetDateTime.of(2026, 8, 3, 7, 20, 34, 0, ZoneOffset.UTC);
	private static final LocalDate DAY = LocalDate.parse("2026-08-03");

	private FakeConsoleFactsRepository repository;

	private MockMvc mvc(ConsoleFacts facts) {
		repository = new FakeConsoleFactsRepository(facts);
		return MockMvcBuilders
				.standaloneSetup(new ConsoleController(new ConsoleFactsService(repository)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	private static TaskRow task(String taskKey, String dataset, String contractKey,
			LocalDate actualAsOf, String freshnessStatus, String freshnessReason) {
		return task(taskKey, dataset, contractKey, DAY, actualAsOf, freshnessStatus, freshnessReason);
	}

	private static TaskRow task(String taskKey, String dataset, String contractKey,
			LocalDate expectedAsOf, LocalDate actualAsOf, String freshnessStatus,
			String freshnessReason) {
		return new TaskRow(taskKey, "etf-daily:2026-08-03T15:40", "etf-daily", DAY, "raw", dataset,
				true, "DUE", "FULFILLED", "VALID", 906L, 0L, 33L, 33L, 0L, 1L,
				contractKey, expectedAsOf, actualAsOf, DB_NOW, freshnessStatus, freshnessReason);
	}

	private static ConsoleFacts facts(List<RunRow> runs, List<TaskRow> tasks) {
		return new ConsoleFacts(DAY, DB_NOW, runs, tasks,
				List.of(new OutputRow("o.pub", "게시 ETF", "종", 16L, 32.0d),
						new OutputRow("o.trig", "배치 트리거", "종", 0L, null)),
				new BoundaryRow(0L, 1L, 114L));
	}

	/**
	 * 계측 없는 축은 <b>키가 없다</b>. {@code chain: null}·{@code queues: []} 로 내려가면 규칙이
	 * "축은 왔는데 비었다"로 읽어 못 돎 대신 <b>평가됨 · 위반 0</b> 을 세운다.
	 */
	@Test
	void 계측_없는_축은_키_자체가_없다() throws Exception {
		RunRow run = new RunRow("etf-daily:2026-08-03T15:40", "etf-daily", DAY, "RUNNING",
				DB_NOW, DB_NOW, null, null);
		String body = mvc(facts(List.of(run), List.of(
				task("ETF_HOLDINGS_COLLECTION_KRX", "etf_holdings", null, null, null, null))))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.meta.today").value("2026-08-03"))
				.andReturn().getResponse().getContentAsString();

		assertThat(body).doesNotContain("\"chain\"").doesNotContain("\"queues\"")
				.doesNotContain("\"etfLedger\"").doesNotContain("\"runbook\"")
				// AWS 제어면은 C 축이다 — 여기서 aws: null 을 내면 "조회했는데 못 봤다"가 된다.
				.doesNotContain("\"aws\"").doesNotContain("\"awsStatus\"")
				.doesNotContain("\"maxRetries\"").doesNotContain("\"syncCursorRows\"");
	}

	/**
	 * {@code planned} 는 런 행이 없는 슬롯에만 실린다. 실재 런에 {@code false} 나 {@code null} 로
	 * 실으면 "계획된 적 없는 런"이라는, 원장이 하지 않은 단정이 화면에 선다.
	 */
	@Test
	void 계획_여부는_런_행이_없는_슬롯에만_실린다() throws Exception {
		RunRow real = new RunRow("etf-daily:2026-08-03T15:40", "etf-daily", DAY, "RUNNING",
				DB_NOW, DB_NOW, null, null);
		RunRow ghost = new RunRow("etf-daily:2026-08-03T21:40", "etf-daily", DAY, null,
				null, null, true, true);

		mvc(facts(List.of(real, ghost), List.of())).perform(get("/api/v1/console/facts"))
				.andExpect(jsonPath("$.result.runs[0].id").value("etf-daily:2026-08-03T15:40"))
				.andExpect(jsonPath("$.result.runs[0].planned").doesNotHaveJsonPath())
				.andExpect(jsonPath("$.result.runs[0].noRunRow").doesNotHaveJsonPath())
				// 원장 상태 없음은 집계 없음이라 키를 유지한 채 null 이다 — 키를 빼면 안 된다.
				.andExpect(jsonPath("$.result.runs[1].ledgerStatus").hasJsonPath())
				.andExpect(jsonPath("$.result.runs[1].ledgerStatus").value(nullValue()))
				.andExpect(jsonPath("$.result.runs[1].planned").value(true))
				.andExpect(jsonPath("$.result.runs[1].noRunRow").value(true));
	}

	/** 실측 0 과 기준 없음(null)은 다른 사실이다 — 둘 다 "값 없음"으로 접히면 안 된다. */
	@Test
	void 산출은_실측_0_과_기준_없음을_가른다() throws Exception {
		mvc(facts(List.of(), List.of())).perform(get("/api/v1/console/facts"))
				.andExpect(jsonPath("$.result.outputs[0].base").value(32.0))
				.andExpect(jsonPath("$.result.outputs[1].today").value(0))
				.andExpect(jsonPath("$.result.outputs[1].base").hasJsonPath())
				.andExpect(jsonPath("$.result.outputs[1].base").value(nullValue()))
				.andExpect(jsonPath("$.result.boundary.publishedWithoutDelivery").value(0))
				.andExpect(jsonPath("$.result.boundary.deliveryRows").value(114));
	}

	/**
	 * 데이터셋 축은 작업에서 파생하고, {@code unverifiable} 은 "신선도를 판정할 수 있는가"의
	 * 정확한 여집합이다. 여집합이 아니면 판정도 못 하고 판정 불가로도 안 잡히는 데이터셋이
	 * 생기는데, 그건 화면에서 <b>정상</b>으로 보인다.
	 */
	@Test
	void 데이터셋_축은_작업에서_파생하고_판정_불가를_빠짐없이_표시한다() throws Exception {
		mvc(facts(List.of(), List.of(
				task("ETF_HOLDINGS_COLLECTION_KRX", "etf_holdings", "ETF_HOLDINGS_KRX_EOD",
						null, "UNKNOWN", "ACTUAL_AS_OF_UNVERIFIED"),
				task("PRICE_COLLECTION_KIS", "price_daily", null, null, null, null),
				task("ETF_NAV_COLLECTION_KIS", "etf_nav", "ETF_NAV_KIS_EOD",
						DAY, "FRESH", null),
				// dataset 이 없거나 빈 작업은 데이터셋 축 자체가 없다 — 빈 키로 세우지 않는다.
				// 빈 id 가 서면 R09 위반의 대상이 비어 엔진이 **R09 전체**를 못 돎으로 세운다.
				task("ASSEMBLE_EVENTS", null, null, null, null, null),
				task("LOAD_DOCUMENTS", "   ", null, null, null, null))))
				.perform(get("/api/v1/console/facts"))
				.andExpect(jsonPath("$.result.datasets.length()").value(3))
				.andExpect(jsonPath("$.result.datasets[0].id").value("etf_holdings"))
				.andExpect(jsonPath("$.result.datasets[0].contract").value(true))
				// 원장이 남긴 사유를 그대로 옮긴다 — 문장으로 바꾸지 않는다(포맷은 UI 소관).
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("ACTUAL_AS_OF_UNVERIFIED"))
				.andExpect(jsonPath("$.result.datasets[1].id").value("etf_nav"))
				.andExpect(jsonPath("$.result.datasets[1].actualAsOf").value("2026-08-03"))
				.andExpect(jsonPath("$.result.datasets[1].unverifiable").value(nullValue()))
				.andExpect(jsonPath("$.result.datasets[2].id").value("price_daily"))
				.andExpect(jsonPath("$.result.datasets[2].contract").value(false))
				.andExpect(jsonPath("$.result.datasets[2].unverifiable")
						.value("CONTRACT_NOT_APPLIED"));
	}

	/**
	 * 원장이 {@code UNKNOWN} 이라고 말했으면 as-of 값이 있어도 판정 불가다. 스키마는 그 조합을
	 * 막지 않는데({@code ck_ops_expected_task_freshness_pair} 는 둘을 안 묶는다), as-of 유무만 보면
	 * 그 데이터셋이 <b>판정 가능</b>으로 서고 값이 기대일과 같으면 R08 도 조용해 두 규칙 다
	 * 위반 0 이 된다 — 아무도 못 본 데이터셋이 정상으로 그려지는 자리다.
	 */
	@Test
	void 원장이_UNKNOWN_이라_말하면_as_of_가_있어도_판정_불가다() throws Exception {
		mvc(facts(List.of(), List.of(
				task("ETF_FLOW_COLLECTION_KRX", "etf_flow", "ETF_FLOW_KRX_EOD",
						DAY, "UNKNOWN", "OBSERVED_AT_MISSING"))))
				.perform(get("/api/v1/console/facts"))
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-03"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable").value("OBSERVED_AT_MISSING"));
	}

	/**
	 * 한 데이터셋에 작업이 여럿이면 값을 어느 방향으로 접느냐가 곧 놓침이냐 과민이냐다.
	 * {@code actualAsOf} 는 <b>가장 오래된 것</b>(하나만 최신이어도 전체가 최신으로 보이면
	 * R08 이 조용해진다), 판정 불가 사유는 <b>UNKNOWN 인 그 작업</b>에서 온다(멀쩡한 작업의
	 * 사유를 갖다 붙이면 서로 다른 작업이 한 사실로 섞인다).
	 */
	@Test
	void 한_데이터셋에_작업이_여럿이면_보수적으로_접는다() throws Exception {
		mvc(facts(List.of(), List.of(
				task("A_FRESH", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, "FRESH", "AS_OF_MATCH"),
				task("B_UNKNOWN", "etf_flow", "ETF_FLOW_KRX_EOD",
						DAY.minusDays(2), "UNKNOWN", "OBSERVED_AT_MISSING"))))
				.perform(get("/api/v1/console/facts"))
				.andExpect(jsonPath("$.result.datasets.length()").value(1))
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("OBSERVED_AT_MISSING"));
	}

	/**
	 * 🔴 as-of 쌍은 <b>한 작업에서 통째로</b> 온다. `expected` 와 `actual` 을 각자 접으면 어느
	 * 작업에도 없던 쌍이 만들어져, 둘 다 FRESH 인 작업 두 개가 거짓 STALE(R08)을 낸다.
	 */
	@Test
	void as_of_쌍을_서로_다른_작업에서_섞지_않는다() throws Exception {
		LocalDate older = DAY.minusDays(2);
		mvc(facts(List.of(), List.of(
				task("A", "etf_flow", "ETF_FLOW_KRX_EOD", older, older, "FRESH", "AS_OF_MATCH"),
				task("B", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, DAY, "FRESH", "AS_OF_MATCH"))))
				.perform(get("/api/v1/console/facts"))
				// 각자 접으면 expected=08-03 · actual=08-01 이라는, 아무도 낸 적 없는 쌍이 선다.
				.andExpect(jsonPath("$.result.datasets[0].expectedAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable").value(nullValue()));
	}

	/**
	 * as-of 가 동률이면 <b>기대일이 늦은 쪽</b>을 고른다 — R08 이 STALE 을 드러내는 방향이다.
	 * tie-break 가 없으면 같은 원장이 조회 순서에 따라 FRESH 로도 STALE 로도 판정된다.
	 */
	@Test
	void as_of_동률이면_STALE_을_드러내는_쪽을_고른다() throws Exception {
		LocalDate older = DAY.minusDays(2);
		mvc(facts(List.of(), List.of(
				task("A", "etf_flow", "ETF_FLOW_KRX_EOD", older, older, "FRESH", "AS_OF_MATCH"),
				task("B", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, older, "FRESH", "AS_OF_MATCH"))))
				.perform(get("/api/v1/console/facts"))
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].expectedAsOf").value("2026-08-03"));
	}

	@Test
	void 날짜는_그대로_내려가고_생략하면_null_이다() throws Exception {
		MockMvc mvc = mvc(facts(List.of(), List.of()));

		mvc.perform(get("/api/v1/console/facts").param("date", "2026-08-01"))
				.andExpect(status().isOk());
		assertThat(repository.requestedDate).isEqualTo(LocalDate.parse("2026-08-01"));

		mvc.perform(get("/api/v1/console/facts")).andExpect(status().isOk());
		assertThat(repository.requestedDate).isNull();
	}

	/** 오타 난 날짜가 아래 계층에서 터지면 500 으로 위장된다 — 게이트에서 400 이다. */
	@Test
	void 날짜_형식이_틀리면_400() throws Exception {
		MockMvc mvc = mvc(facts(List.of(), List.of()));

		mvc.perform(get("/api/v1/console/facts").param("date", "2026-8-3"))
				.andExpect(status().isBadRequest());
		mvc.perform(get("/api/v1/console/facts").param("date", "+999999999-12-31"))
				.andExpect(status().isBadRequest());
	}

	/**
	 * 아직 오지 않은 날의 산출은 <b>실측 0 이 아니라 "아직"</b>인데 이 응답에는 그 둘을 가르는
	 * 자리가 없다. 통과시키면 R13 이 다섯 산출을 −100% 로 판정해 거짓 P1 이 선다.
	 *
	 * <p>어제는 통과해야 한다 — 상한이 원장의 최신 거래일로 좁아지면 <b>계획이 통째로 안 돈 날</b>
	 * 을 못 열게 되고, 그날이 바로 R01 이 P0 를 내야 하는 날이다.
	 */
	@Test
	void 미래_날짜는_400_이고_지난_날짜는_통과한다() throws Exception {
		MockMvc mvc = mvc(facts(List.of(), List.of()));
		LocalDate todayKst = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));

		mvc.perform(get("/api/v1/console/facts").param("date", todayKst.plusDays(1).toString()))
				.andExpect(status().isBadRequest());
		mvc.perform(get("/api/v1/console/facts").param("date", todayKst.toString()))
				.andExpect(status().isOk());
		mvc.perform(get("/api/v1/console/facts").param("date", todayKst.minusDays(1).toString()))
				.andExpect(status().isOk());

		/* 위 셋은 프로덕션과 **같은 식**(`LocalDate.now(KST)`)을 써서 게이트의 존재·폭은 잡지만
		 * 존을 바꾸는 변이는 못 잡는다(리뷰 지적). 존과 무관하게 성립하는 두 고정 날짜를 함께 둔다. */
		mvc.perform(get("/api/v1/console/facts").param("date", "9999-12-31"))
				.andExpect(status().isBadRequest());
		mvc.perform(get("/api/v1/console/facts").param("date", "2020-01-01"))
				.andExpect(status().isOk());
	}
}
