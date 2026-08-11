package com.edge.superadmin.controller;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.repository.ConsoleFactsRepository.ConsoleFacts;
import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
import com.edge.superadmin.repository.ConsoleFactsRepository.TaskRow;
import com.edge.superadmin.service.ConsoleFactsService;
import com.edge.superadmin.support.FakeConsoleFactsRepository;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 콘솔 사실 응답의 <b>조회 창 + 런 축 + 작업 축 + 데이터셋 축</b> 계약(ALPHA-738).
 *
 * <p>지키는 것 셋 — ① 요청한 날이 <b>그대로 아래로 내려가는가</b>(게이트가 값을 조용히 바꾸면
 * 화면은 다른 날을 보고도 모른다) ② 원장이 <b>실제로 무엇을 봤는지</b> 되돌려주는가 ③ 런 축이
 * <b>원장 값 그대로</b> 나가는가(어휘를 다시 정의하면 판정이 서버로 샌다).
 *
 * <p>데이터셋 축만 다르다 — <b>여러 행을 하나로 접는 유일한 축</b>이라(원장에 그 테이블이 없다)
 * 접는 <b>방향</b>이 곧 계약이다. 그래서 그 절의 단언은 값의 존재가 아니라 <b>어느 작업의 값을
 * 골랐는가</b>를 잰다.
 *
 * <p>그리고 <b>부재의 종류</b>: 아직 안 붙은 축은 빈 배열이 아니라 <b>키가 없고</b>, 값을 못 구한
 * 필드는 <b>키가 있고 null</b> 이다. 둘이 뭉개지면 화면이 계측 공백을 "봤고 괜찮다"로 그린다.
 * 그래서 JSON 문자열의 키 유무까지 본다 — 자바 타입 단언으로는 Jackson 설정 한 줄이 바꿔 놓는
 * 것을 못 잡는다.
 */
class ConsoleControllerTest {

	private static final OffsetDateTime DB_NOW =
			OffsetDateTime.of(2026, 8, 3, 7, 20, 34, 0, ZoneOffset.UTC);
	private static final LocalDate DAY = LocalDate.parse("2026-08-03");

	private FakeConsoleFactsRepository repository;

	private static ConsoleFacts facts(RunRow... runs) {
		return new ConsoleFacts(DAY, DB_NOW, List.of(runs), List.of());
	}

	private static ConsoleFacts factsWithTask(TaskRow... tasks) {
		return new ConsoleFacts(DAY, DB_NOW, List.of(), List.of(tasks));
	}

	/** 계약·신선도 여섯 컬럼은 <b>데이터셋 축의 재료</b>다 — 작업 축 와이어에 안 나간다. */
	private static TaskRow task(Long recordsOut, Long completenessExpected) {
		return new TaskRow("COLLECT", "etf-daily:2026-08-03T15:40", "etf-daily", DAY, "raw",
				"price", true, "DUE", "FULFILLED", "VALID", recordsOut, 0L, completenessExpected,
				33L, 0L, 2L, "price@v1", DAY, DAY, DB_NOW, "FRESH", "AS_OF_MATCH");
	}

	/**
	 * 데이터셋 축 재료가 되는 작업.
	 *
	 * <p>⚠️ 픽스처가 <b>스키마에 없는 조합</b>을 만들면 그 위에서 통과한 단언은 운영 원장을 아무것도
	 * 못 지킨다. 그래서 {@code ck_ops_expected_task_*} 를 따라간다 — 계약이 없으면
	 * {@code collectedAt} 도 신선도도 없고(applicability), {@code FRESH} 는 {@code actual =
	 * expected} 일 때만, {@code STALE} 은 {@code actual < expected} 일 때만 성립한다
	 * (verified_as_of). 그래서 as-of 가 갈리는 픽스처의 상태는 {@code STALE}·{@code UNKNOWN} 이다.
	 */
	private static TaskRow task(String taskKey, String dataset, String contractKey,
			LocalDate expectedAsOf, LocalDate actualAsOf, String freshnessStatus,
			String freshnessReason) {
		return task(taskKey, dataset, contractKey, expectedAsOf, actualAsOf,
				contractKey == null ? null : DB_NOW, freshnessStatus, freshnessReason);
	}

	/**
	 * ⚠️ {@code collectedAt} 을 <b>따로 받는다</b>. 전건을 {@code DB_NOW} 로 박으면 그 값을 어느
	 * 방향으로 접든(max·min·기준 작업의 것) 결과가 같아 <b>접기 자체가 안 재진다</b>(리뷰가 잡았다 —
	 * 픽스처가 컬럼을 못 가르면 그 컬럼은 계약이 아니다).
	 */
	private static TaskRow task(String taskKey, String dataset, String contractKey,
			LocalDate expectedAsOf, LocalDate actualAsOf, OffsetDateTime collectedAt,
			String freshnessStatus, String freshnessReason) {
		return task(taskKey, dataset, contractKey, "DUE", expectedAsOf, actualAsOf, collectedAt,
				freshnessStatus, freshnessReason);
	}

	/**
	 * ⚠️ {@code planStatus} 를 <b>따로 받는다</b>. 전건 {@code DUE} 로 박으면 신선도를 {@code DUE}
	 * 로 좁히는 계약이 안 재진다 — 휴장일({@code SKIPPED})이 원장에 실제로 들어오는 형태다.
	 */
	private static TaskRow task(String taskKey, String dataset, String contractKey,
			String planStatus, LocalDate expectedAsOf, LocalDate actualAsOf,
			OffsetDateTime collectedAt, String freshnessStatus, String freshnessReason) {
		return new TaskRow(taskKey, "etf-daily:2026-08-03T15:40", "etf-daily", DAY, "raw", dataset,
				true, planStatus, "FULFILLED", "VALID", 906L, 0L, 33L, 33L, 0L, 1L,
				contractKey, expectedAsOf, actualAsOf, collectedAt, freshnessStatus,
				freshnessReason);
	}

	private MockMvc mvc(ConsoleFacts facts) {
		repository = new FakeConsoleFactsRepository(facts);
		return MockMvcBuilders
				.standaloneSetup(new ConsoleController(new ConsoleFactsService(repository)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	/** 화면은 요청한 날짜가 아니라 <b>이 값</b>을 그린다 — 서버가 다른 날을 골랐을 때 거짓말이 안 되게. */
	@Test
	void 무엇을_본_응답인가를_되돌려준다() throws Exception {
		mvc(facts())
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.meta.today").value("2026-08-03"))
				.andExpect(jsonPath("$.result.meta.db").value(DB_NOW.toString()));
	}

	/**
	 * 아직 안 싣는 축은 <b>키가 없다</b>. ⚠️ 런 축은 이제 붙었으므로 이 목록에서 빠졌다 — 런이
	 * 0건인 날의 {@code runs: []} 는 "봤는데 없었다"는 <b>사실</b>이고 계측 공백과 반대다.
	 * {@code tasks: []} 로 내려가면 규칙 층이 "축은 왔는데
	 * 비었다"로 읽어 <b>못 돎</b> 대신 "평가됨 · 위반 0" 을 세운다 — 계측 공백이 정상으로 뒤집힌다.
	 * 축을 하나씩 더하는 이 트랙에서 그 구분이 곧 진행 상태의 정본이라, 키 부재를 여기서 못 박는다.
	 */
	@Test
	void 아직_없는_축은_빈_배열이_아니라_키가_없다() throws Exception {
		String body = mvc(facts())
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(content().contentTypeCompatibleWith("application/json"))
				.andReturn().getResponse().getContentAsString();

		/* 문자열로 본다 — `jsonPath(...).doesNotExist()` 는 `"runs": null` 도 통과시켜서
		 * "계측 없음"과 "집계 없음"을 가르지 못한다. */
		assertThat(body).doesNotContain("\"outputs\"", "\"boundary\"");
		/* 🔴 **셋째 다리**: 런이 0건인 날의 `runs: []` 는 **사실**이라 키가 있어야 한다("봤는데
		 * 없었다"). 이걸 안 재면 `NON_EMPTY` 한 줄에 키가 통째로 사라져 규칙 층이 "아직 안 봄"
		 * 으로 읽는데 전건 초록이다 — 부재 3분 중 이 다리만 비어 있었다.
		 *
		 * ⚠️ `datasets` 도 이제 붙었으므로 위 목록에서 빠지고 이쪽으로 왔다. 작업이 0건이면 이 축은
		 * "묶을 게 없었다"라 빈 배열이 맞다 — 파생 축이라고 키를 빼면 안 된다. */
		assertThat(body).contains("\"runs\":[]", "\"tasks\":[]", "\"datasets\":[]");
	}

	/**
	 * 데이터셋 축은 작업에서 <b>파생</b>하고, {@code unverifiable} 은 "신선도를 판정할 수 있는가"의
	 * 여집합보다 <b>한 겹 넓다</b>. 여집합보다 좁으면 판정도 못 하고 판정 불가로도 안 잡히는
	 * 데이터셋이 생기는데, 그건 화면에서 <b>정상</b>으로 보인다.
	 *
	 * <p>정렬은 데이터셋 id 다 — 안 고정하면 같은 원장이 조회마다 다른 순서로 나가고, 소비자가
	 * "첫 데이터셋"을 집는 순간 판정이 흔들린다. 그래서 입력을 <b>id 역순으로 넣는다</b>(그냥
	 * 순서대로 넣으면 정렬을 지워도 통과한다).
	 */
	@Test
	void 데이터셋_축은_작업에서_파생하고_판정_불가를_빠짐없이_표시한다() throws Exception {
		mvc(factsWithTask(
				task("PRICE_COLLECTION_KIS", "price_daily", null, DAY, null, null, null),
				task("ETF_NAV_COLLECTION_KIS", "etf_nav", "ETF_NAV_KIS_EOD", DAY, DAY, "FRESH",
						"AS_OF_MATCH"),
				task("ETF_HOLDINGS_COLLECTION_KRX", "etf_holdings", "ETF_HOLDINGS_KRX_EOD", DAY,
						null, "UNKNOWN", "ACTUAL_AS_OF_UNVERIFIED"),
				// dataset 이 없거나 빈 작업은 데이터셋 축 자체가 없다 — 빈 키로 세우지 않는다.
				// 빈 id 가 서면 그걸 위반으로 만드는 규칙이 빈 대상 가드에 걸려 통째로 못 돎이 된다.
				task("ASSEMBLE_EVENTS", null, null, null, null, null, null),
				task("LOAD_DOCUMENTS", "   ", null, null, null, null, null)))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets.length()").value(3))
				.andExpect(jsonPath("$.result.datasets[0].id").value("etf_holdings"))
				.andExpect(jsonPath("$.result.datasets[0].contract").value(true))
				// 원장이 남긴 사유를 그대로 옮긴다 — 문장으로 바꾸지 않는다(포맷은 UI 소관).
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("ACTUAL_AS_OF_UNVERIFIED"))
				.andExpect(jsonPath("$.result.datasets[1].id").value("etf_nav"))
				.andExpect(jsonPath("$.result.datasets[1].actualAsOf").value("2026-08-03"))
				.andExpect(jsonPath("$.result.datasets[1].collectedAt").value(DB_NOW.toString()))
				.andExpect(jsonPath("$.result.datasets[1].unverifiable").value(nullValue()))
				.andExpect(jsonPath("$.result.datasets[2].id").value("price_daily"))
				.andExpect(jsonPath("$.result.datasets[2].contract").value(false))
				.andExpect(jsonPath("$.result.datasets[2].unverifiable")
						.value("CONTRACT_NOT_APPLIED"));
	}

	/**
	 * 🔴 <b>원장이 {@code UNKNOWN} 이라고 말했으면 as-of 값이 있어도 판정 불가다.</b> 스키마는 그
	 * 조합을 막지 않는데({@code ck_ops_expected_task_verified_as_of} 는 {@code actual > expected}
	 * 인 UNKNOWN 을 허용한다), as-of 유무만 보면 그 데이터셋이 <b>판정 가능</b>으로 서고 값이
	 * 낡음 규칙도 통과하면 두 규칙 다 위반 0 이 된다 — 아무도 못 본 데이터셋이 정상으로 그려진다.
	 */
	@Test
	void 원장이_UNKNOWN_이라_말하면_as_of_가_있어도_판정_불가다() throws Exception {
		mvc(factsWithTask(task("ETF_FLOW_COLLECTION_KRX", "etf_flow", "ETF_FLOW_KRX_EOD",
				DAY.minusDays(2), DAY, "UNKNOWN", "OBSERVED_AT_MISSING")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-03"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("OBSERVED_AT_MISSING"));
	}

	/**
	 * 한 데이터셋에 작업이 여럿이면 값을 어느 방향으로 접느냐가 곧 놓침이냐 과민이냐다.
	 * {@code actualAsOf} 는 <b>가장 오래된 것</b>(하나만 최신이어도 전체가 최신으로 보이면 낡음이
	 * 조용해진다), 판정 불가 사유는 <b>UNKNOWN 인 그 작업</b>에서 온다 — 멀쩡한 작업의
	 * {@code AS_OF_MATCH} 를 갖다 붙이면 서로 다른 작업이 한 사실로 섞인다.
	 */
	@Test
	void 한_데이터셋에_작업이_여럿이면_보수적으로_접는다() throws Exception {
		mvc(factsWithTask(
				task("A_FRESH", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, DAY, "FRESH", "AS_OF_MATCH"),
				task("B_UNKNOWN", "etf_flow", "ETF_FLOW_KRX_EOD", DAY.minusDays(3),
						DAY.minusDays(2), "UNKNOWN", "OBSERVED_AT_MISSING"),
				/* 🔴 **계약 없는 작업을 같은 데이터셋에 섞는다** — `contract` 는 `anyMatch` 라야
				 * 한다. 전건이 계약을 가지면 `allMatch` 로 바꿔도 통과하고, 그러면 운영에서
				 * 조립 스텝 하나가 섞인 데이터셋이 통째로 CONTRACT_NOT_APPLIED 로 뒤집힌다.
				 * 계약 없는 행은 스키마상 as-of·수집시각·신선도가 전부 NULL 이다. */
				task("C_NO_CONTRACT", "etf_flow", null, null, null, null, null, null)))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets.length()").value(1))
				.andExpect(jsonPath("$.result.datasets[0].contract").value(true))
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("OBSERVED_AT_MISSING"));
	}

	/**
	 * 🔴 as-of 쌍은 <b>한 작업에서 통째로</b> 온다. {@code expected} 와 {@code actual} 을 각자
	 * 접으면(max·min) 어느 작업에도 없던 쌍이 만들어져, 둘 다 FRESH 인 작업 두 개가 <b>거짓
	 * STALE</b> 을 낸다.
	 */
	@Test
	void as_of_쌍을_서로_다른_작업에서_섞지_않는다() throws Exception {
		LocalDate older = DAY.minusDays(2);
		mvc(factsWithTask(
				task("A", "etf_flow", "ETF_FLOW_KRX_EOD", older, older, "FRESH", "AS_OF_MATCH"),
				task("B", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, DAY, "FRESH", "AS_OF_MATCH")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				// 각자 접으면 expected=08-03 · actual=08-01 이라는, 아무도 낸 적 없는 쌍이 선다.
				.andExpect(jsonPath("$.result.datasets[0].expectedAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable").value(nullValue()));
	}

	/**
	 * as-of 가 동률이면 <b>기대일이 늦은 쪽</b>을 고른다 — 낡음을 드러내는 방향이다. tie-break 가
	 * 없으면 같은 원장이 조회 순서에 따라 FRESH 로도 STALE 로도 판정된다.
	 */
	@Test
	void as_of_동률이면_낡음을_드러내는_쪽을_고른다() throws Exception {
		LocalDate older = DAY.minusDays(2);
		mvc(factsWithTask(
				task("A", "etf_flow", "ETF_FLOW_KRX_EOD", older, older, "FRESH", "AS_OF_MATCH"),
				task("B", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, older, "STALE",
						"ACTUAL_AS_OF_BEFORE_EXPECTED")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].expectedAsOf").value("2026-08-03"))
				// 낡음 판정은 클라이언트 소관이다 — 판정 가능하면 여기서는 null 이다.
				.andExpect(jsonPath("$.result.datasets[0].unverifiable").value(nullValue()));
	}

	/**
	 * 🔴 <b>as-of 근거가 아예 없으면 그때만 {@code expected} 를 따로 접는다 — 가장 늦은 쪽으로.</b>
	 * 이르게 접으면 기대일이 실제보다 과거로 보여 낡음 규칙이 조용해진다.
	 *
	 * <p>그리고 <b>{@code collectedAt} 은 가장 최근</b>이다 — "언제까지 관측 가능해졌나"라 늦은
	 * 쪽이 답이다. 두 값을 픽스처에서 갈라 둬야 접는 방향이 실제로 재진다.
	 */
	@Test
	void actual_이_없으면_expected_와_collectedAt_을_늦은_쪽으로_접는다() throws Exception {
		OffsetDateTime later = DB_NOW.plusHours(2);
		/* ⚠️ 기대일 최댓값(08-01)을 런의 거래일(08-03)과 **다르게** 둔다 — 같으면 이 접기를
		 * `tradingDate` 로 바꾸는 변이가 통과한다(리뷰가 잡았다). */
		mvc(factsWithTask(
				task("A", "etf_flow", "ETF_FLOW_KRX_EOD", DAY.minusDays(4), null, DB_NOW,
						"UNKNOWN", "ACTUAL_AS_OF_UNVERIFIED"),
				task("B", "etf_flow", "ETF_FLOW_KRX_EOD", DAY.minusDays(2), null, later,
						"UNKNOWN", "ACTUAL_AS_OF_UNVERIFIED")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets.length()").value(1))
				.andExpect(jsonPath("$.result.datasets[0].expectedAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value(nullValue()))
				.andExpect(jsonPath("$.result.datasets[0].collectedAt").value(later.toString()))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("ACTUAL_AS_OF_UNVERIFIED"));
	}

	/**
	 * 🔴 <b>휴장일은 증거 결손이 아니다.</b> Planner 는 비거래일 작업에도 계약 키를 남기되
	 * {@code plan_status='SKIPPED'} 로 두고 신선도를 안 쓴다({@code ops/planner.py}) — 그 NULL 은
	 * 마이그레이션이 정의한 대로 <b>NOT_APPLICABLE 이고 UNKNOWN 과 다르다</b>. 전건을 그냥 접으면
	 * "계약은 있는데 근거가 없다"로 서서 <b>정상 휴장일마다 거짓 경보</b>가 난다(리뷰가 잡았다).
	 *
	 * <p>{@code SKIPPED} 행은 스키마상 as-of·수집시각·신선도가 전부 NULL 이다
	 * ({@code ck_ops_expected_task_freshness_applicability}) — 픽스처가 그 형태를 지킨다.
	 */
	@Test
	void 실행_대상이_아니었던_날은_증거_결손이_아니다() throws Exception {
		mvc(factsWithTask(task("COLLECT", "etf_flow", "ETF_FLOW_KRX_EOD", "SKIPPED",
				DAY, null, null, null, null)))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets[0].contract").value(true))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable").value("NOT_APPLICABLE"));
	}

	/**
	 * 반대편: 같은 데이터셋에 {@code DUE} 가 하나라도 있으면 그 작업들로 판정한다 —
	 * {@code SKIPPED} 행이 접기에 끼어들어 값을 흐리면 안 된다.
	 */
	@Test
	void SKIPPED_행은_DUE_작업의_판정에_끼어들지_않는다() throws Exception {
		mvc(factsWithTask(
				task("SKIPPED_ONE", "etf_flow", "ETF_FLOW_KRX_EOD", "SKIPPED",
						DAY, null, null, null, null),
				task("DUE_ONE", "etf_flow", "ETF_FLOW_KRX_EOD", DAY.minusDays(2),
						DAY.minusDays(2), DB_NOW, "FRESH", "AS_OF_MATCH")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				// SKIPPED 의 기대일(08-03)이 아니라 DUE 의 쌍(08-01/08-01)이 나가야 한다.
				.andExpect(jsonPath("$.result.datasets[0].expectedAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable").value(nullValue()));
	}

	/**
	 * 🔴 <b>기준 작업이 UNKNOWN 이 아니어도 나머지에서 UNKNOWN 을 찾는다.</b> 기준 작업 하나만 보면
	 * 더 오래된 FRESH 작업이 앞에 서는 순간 그 데이터셋이 <b>판정 가능</b>으로 인증되고, 원장이
	 * UNKNOWN 이라 말한 작업은 아무도 못 본다(리뷰가 잡았다 — {@code Stream.concat} 의 뒷항이
	 * 지키는 계약이다).
	 */
	@Test
	void 기준_작업이_UNKNOWN_이_아니어도_나머지에서_찾는다() throws Exception {
		LocalDate older = DAY.minusDays(2);
		mvc(factsWithTask(
				// 기준 작업(actual 이 가장 오래됨)이지만 FRESH 다.
				task("A_FRESH", "etf_flow", "ETF_FLOW_KRX_EOD", older, older, DB_NOW,
						"FRESH", "AS_OF_MATCH"),
				// actual 이 더 최신이라 기준이 아니지만 원장이 UNKNOWN 이라 말했다.
				task("B_UNKNOWN", "etf_flow", "ETF_FLOW_KRX_EOD", older, DAY, DB_NOW,
						"UNKNOWN", "OBSERVED_AT_MISSING")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("OBSERVED_AT_MISSING"));
	}

	/**
	 * 🔴 <b>판정 불가 사유는 as-of 쌍을 준 그 작업에서 온다.</b> UNKNOWN 인 작업이 둘 이상일 때
	 * 사유를 따로 고르면, 한 행이 B 의 as-of 와 A 의 사유를 함께 실어 <b>서로 다른 작업이 한
	 * 사실로 섞인다</b>. 스키마가 그 조합을 허용한다 — {@code ck_ops_expected_task_verified_as_of}
	 * 는 {@code actual > expected} 인 UNKNOWN 을 통과시키므로 as-of 를 가진 UNKNOWN 이 존재한다.
	 *
	 * <p>기준 작업을 <b>먼저</b> 넣지 않는다(입력 순서로 통과하면 고른 게 아니다).
	 */
	@Test
	void 판정_불가_사유는_as_of_를_준_작업에서_온다() throws Exception {
		mvc(factsWithTask(
				// as-of 가 없는 UNKNOWN — 목록에서 먼저지만 기준 작업이 아니다.
				task("A_NO_ASOF", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, null, DB_NOW,
						"UNKNOWN", "ACTUAL_AS_OF_UNVERIFIED"),
				// 유일하게 as-of 를 가진 작업 — 쌍도 사유도 여기서 와야 한다.
				task("B_HAS_ASOF", "etf_flow", "ETF_FLOW_KRX_EOD", DAY.minusDays(2), DAY, DB_NOW,
						"UNKNOWN", "ACTUAL_AS_OF_AFTER_EXPECTED")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-03"))
				.andExpect(jsonPath("$.result.datasets[0].expectedAsOf").value("2026-08-01"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("ACTUAL_AS_OF_AFTER_EXPECTED"));
	}

	/**
	 * 🔴 <b>as-of 쌍이 완전히 동률이면 작업 키가 기준 작업을 정한다.</b> 쌍은 어느 쪽을 골라도
	 * 같지만 <b>사유가 그 선택을 따라오므로</b>, tie-break 가 없으면 같은 원장이 조회 순서에 따라
	 * 서로 다른 사유를 낸다. 그래서 입력을 <b>작업 키 역순</b>으로 넣는다.
	 */
	@Test
	void as_of_가_완전_동률이면_작업_키가_사유를_정한다() throws Exception {
		mvc(factsWithTask(
				task("B_LATER", "etf_flow", "ETF_FLOW_KRX_EOD", DAY.minusDays(2), DAY, DB_NOW,
						"UNKNOWN", "OBSERVED_AT_MISSING"),
				task("A_FIRST", "etf_flow", "ETF_FLOW_KRX_EOD", DAY.minusDays(2), DAY, DB_NOW,
						"UNKNOWN", "ACTUAL_AS_OF_AFTER_EXPECTED")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("ACTUAL_AS_OF_AFTER_EXPECTED"));
	}

	/**
	 * 🔴 <b>빈 사유는 사유가 아니다.</b> {@code ck_ops_expected_task_freshness_pair} 는 UNKNOWN 의
	 * {@code freshness_reason} 에 {@code IS NOT NULL} 만 걸어 <b>빈 문자열을 막지 않는다</b>.
	 * 그대로 내리면 판정 코드를 truthy 로 보는 소비자가 판정 불가 데이터셋을 <b>정상으로 건너뛴다</b> —
	 * 이 축이 없애려는 바로 그 실패다. 판정 불가는 유지하고 사유만 기본 코드로 떨어진다.
	 *
	 * <p>🔴 그 기본 코드는 {@code ACTUAL_AS_OF_MISSING} 이 <b>아니다</b>. UNKNOWN 은 as-of 가
	 * 있어도 서므로(여기 픽스처가 그렇다) 그 코드를 돌려쓰면 응답이 <b>actual 날짜를 실은 채</b>
	 * "as-of 가 없다"고 말하게 된다 — 판정 불가는 맞지만 사유가 거짓이라 운영자가 없는 결손을
	 * 찾으러 간다(리뷰가 잡았다).
	 */
	@Test
	void 빈_사유는_판정_불가를_지우지_않되_없는_결손을_지어내지도_않는다() throws Exception {
		mvc(factsWithTask(task("COLLECT", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, DAY, DB_NOW,
				"UNKNOWN", "   ")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				// as-of 가 실제로 있다 — 그런데 사유가 "as-of 없음"이면 그 사유가 거짓이다.
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value("2026-08-03"))
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("FRESHNESS_REASON_MISSING"));
	}

	/**
	 * 대비: 계약은 있는데 <b>as-of 근거 자체가 없는</b> 자리는 {@code ACTUAL_AS_OF_MISSING} 이다.
	 * 두 코드가 서로 다른 사실을 가리키므로 한쪽으로 뭉개면 안 된다.
	 */
	@Test
	void as_of_근거가_없는_것과_사유가_없는_것은_다른_코드다() throws Exception {
		mvc(factsWithTask(task("COLLECT", "etf_flow", "ETF_FLOW_KRX_EOD", DAY, null, DB_NOW,
				"UNKNOWN", "ACTUAL_AS_OF_UNVERIFIED")))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.datasets[0].actualAsOf").value(nullValue()))
				// 원장이 사유를 남겼으면 그게 이긴다 — 기본 코드로 덮지 않는다.
				.andExpect(jsonPath("$.result.datasets[0].unverifiable")
						.value("ACTUAL_AS_OF_UNVERIFIED"));
	}

	/**
	 * 🔴 <b>dataset 이 빈 작업은 축에서 빼되 조용히 빼지 않는다</b>(Rule 12). 스키마에 비공백 제약이
	 * 없어 원장에 실제로 들어올 수 있고, 그건 writer 결함이다 — 로그가 없으면 데이터셋 하나가
	 * 화면에서 통째로 사라진 것을 아무도 못 본다. 그 작업 자체는 {@code tasks[]} 에 그대로 남는다.
	 *
	 * <p>⚠️ 경고는 <b>요청당 한 줄</b>이다. 콘솔은 주기적으로 재조회하고 운영자가 여럿이라, 행마다
	 * 찍으면 원장 결함 하나가 로그량을 요청량 × 작업 수로 증폭한다. 그래서 빈 작업을 <b>둘</b> 넣고
	 * 줄 수를 정확히 센다 — 하나만 넣으면 행마다 찍는 퇴행도 한 줄이라 안 걸린다(리뷰가 잡았다).
	 */
	@Test
	void dataset_이_빈_작업은_축에서_빼고_요청당_한_줄로_경고한다() throws Exception {
		Logger logger = (Logger) LoggerFactory.getLogger(ConsoleFactsService.class);
		ListAppender<ILoggingEvent> appender = new ListAppender<>();
		appender.start();
		logger.addAppender(appender);
		try {
			mvc(factsWithTask(
					task("LOAD_DOCUMENTS", "   ", null, null, null, null, null),
					task("ASSEMBLE_EVENTS", "", null, null, null, null, null)))
					.perform(get("/api/v1/console/facts"))
					.andExpect(status().isOk())
					.andExpect(jsonPath("$.result.datasets.length()").value(0))
					// 작업 축에는 그대로 남는다 — 데이터셋 축이 없다고 작업이 없어지지 않는다.
					.andExpect(jsonPath("$.result.tasks.length()").value(2));
		} finally {
			logger.detachAppender(appender);
		}

		assertThat(appender.list).singleElement()
				.satisfies(event -> assertThat(event.getFormattedMessage())
						.contains("LOAD_DOCUMENTS", "ASSEMBLE_EVENTS"));
	}

	/**
	 * 런 축은 <b>원장 컬럼 그대로</b> 나간다. 표시 문자열을 만들거나 어휘를 다시 정의하면 판정이
	 * 서버로 새어 들어온다 — 이 응답의 일은 사실을 옮기는 것까지다.
	 *
	 * <p>{@code id} 는 {@code run_key} 여야 한다(내부 id 가 아니라) — 사건 식별자의 대상 축이고
	 * 뒤에 붙을 작업 축이 이 값으로 조인한다.
	 */
	@Test
	void 런_축은_원장_값을_그대로_순서대로_싣는다() throws Exception {
		/* 런을 **둘** 넣는 이유: 정렬 단언이 리포지토리 레벨에만 있으면 서비스가 순서를 뒤집어도
		 * 아무도 못 본다(`.sorted(...)` 한 줄이면 된다). 계약이 정렬을 약속하는 대상은 소비자,
		 * 곧 **와이어**다. 조각 3 에서 서비스가 실제 파생 로직을 갖게 되면 그때 물리는 자리다. */
		mvc(facts(
				new RunRow("etf-daily:2026-08-03T15:40", "etf-daily", DAY, "SUCCEEDED",
						DB_NOW, DB_NOW.plusHours(1), null, null),
				new RunRow("news:2026-08-03T15:30", "news", DAY, "RUNNING", DB_NOW, null, null, null)))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.runs.length()").value(2))
				.andExpect(jsonPath("$.result.runs[1].id").value("news:2026-08-03T15:30"))
				.andExpect(jsonPath("$.result.runs[0].id").value("etf-daily:2026-08-03T15:40"))
				.andExpect(jsonPath("$.result.runs[0].lane").value("etf-daily"))
				.andExpect(jsonPath("$.result.runs[0].tradingDate").value("2026-08-03"))
				.andExpect(jsonPath("$.result.runs[0].ledgerStatus").value("SUCCEEDED"))
				.andExpect(jsonPath("$.result.runs[0].ledgerUpdated").value(DB_NOW.toString()))
				.andExpect(jsonPath("$.result.runs[0].deadline")
						.value(DB_NOW.plusHours(1).toString()));
	}

	/**
	 * 🔴 <b>모르는 값은 null 로 실린다 — 키를 빼지 않는다.</b> 거래일 없는 런(비거래일 레인)이나
	 * 마감이 없는 런이 그렇다. 키를 빼면 규칙 층이 "계측 없음"으로 읽어 그 축 규칙을 통째로
	 * <b>못 돎</b> 으로 세운다 — 값을 못 구한 것과 아예 안 재는 것은 다른 사실이다.
	 */
	@Test
	void 런의_모르는_값은_null_로_싣는다_키를_빼지_않는다() throws Exception {
		String body = mvc(facts(new RunRow("news:2026-08-03T15:30", "news", null, "RUNNING",
				DB_NOW, null, null, null)))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andReturn().getResponse().getContentAsString();

		/* `jsonPath(...).doesNotExist()` 는 null 도 통과시켜 "키 있고 null" 과 "키 없음" 을 못
		 * 가른다 — 문자열로 키의 실재를 확인한다. */
		assertThat(body).contains("\"tradingDate\":null", "\"deadline\":null");
	}

	/**
	 * 🔴 <b>`planned`·`noRunRow` 는 계획 슬롯에만 실린다.</b> 실재 런에 `false` 를 채우면 "계획된
	 * 적 없다"는 <b>단정</b>이 되는데, 그걸 답할 계측이 원장에 없다(크론 설정은 DB 밖이다).
	 * 그래서 실재 런에서는 <b>키 자체를 뺀다</b> — 이 응답에서 `@JsonInclude` 를 필드 단위로 거는
	 * 유일한 자리이고, 클래스 단위로 올리면 다른 축의 "집계 없음(null)"까지 같이 지워진다.
	 */
	@Test
	void 계획_여부는_런_행이_없는_슬롯에만_실린다() throws Exception {
		String body = mvc(facts(
				new RunRow("etf-daily:2026-08-03T15:40", "etf-daily", DAY, "SUCCEEDED",
						DB_NOW, null, null, null),
				new RunRow("etf-daily:2026-08-03T09:00", "etf-daily", DAY, null, null, null,
						true, true)))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.runs[1].planned").value(true))
				.andExpect(jsonPath("$.result.runs[1].noRunRow").value(true))
				.andReturn().getResponse().getContentAsString();

		/* 실재 런 쪽에 키가 **없어야** 한다. `jsonPath(...).doesNotExist()` 는 null 도 통과시켜
		 * "키 있고 null" 을 못 가르므로 JSON 조각을 직접 본다. */
		assertThat(body).contains("\"id\":\"etf-daily:2026-08-03T15:40\"")
				.doesNotContain("\"planned\":null", "\"noRunRow\":null");
	}

	/**
	 * 작업 축은 원장 값을 그대로 싣되, {@code TaskRow} 의 <b>뒤쪽 여섯 컬럼</b>(계약·신선도)은
	 * 와이어에 <b>안 나간다</b> — 그건 데이터셋 축을 파생하는 재료이지 작업 축의 사실이 아니다.
	 * 그대로 흘리면 소비자가 같은 사실을 두 축에서 읽고 한쪽만 고칠 때 갈린다.
	 *
	 * <p>🔴 그리고 <b>모르는 값은 null 로 실린다</b>. {@code getLong} 이 SQL NULL 을 0 으로 주는
	 * 자리라, 접히면 "0건 처리"와 "신호 없음"이 화면에서 같은 칸이 된다.
	 */
	@Test
	void 작업_축은_계약_컬럼을_안_싣고_모르는_값은_null_이다() throws Exception {
		String body = mvc(factsWithTask(task(null, null)))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.tasks[0].taskKey").value("COLLECT"))
				/* 런 축과 같은 축으로 매인다 — 내부 id 면 와이어에서 안 이어진다. */
				.andExpect(jsonPath("$.result.tasks[0].runId").value("etf-daily:2026-08-03T15:40"))
				.andExpect(jsonPath("$.result.tasks[0].attempts").value(2))
				/* ⚠️ 여섯 컬럼 중 셋은 이제 **데이터셋 축에 이름이 같은 필드로 존재한다** — 그래서
				 * 응답 문자열 전체로 재면 안 되고 **작업 객체 안**을 봐야 한다. 여기서 통째 검사로
				 * 두면 데이터셋 축이 붙는 순간 이 단언이 계약이 아니라 잡음이 된다. */
				.andExpect(jsonPath("$.result.tasks[0].datasetContractKey").doesNotHaveJsonPath())
				.andExpect(jsonPath("$.result.tasks[0].expectedAsOf").doesNotHaveJsonPath())
				.andExpect(jsonPath("$.result.tasks[0].actualAsOf").doesNotHaveJsonPath())
				.andExpect(jsonPath("$.result.tasks[0].collectedAt").doesNotHaveJsonPath())
				.andExpect(jsonPath("$.result.tasks[0].freshnessStatus").doesNotHaveJsonPath())
				.andExpect(jsonPath("$.result.tasks[0].freshnessReason").doesNotHaveJsonPath())
				.andReturn().getResponse().getContentAsString();

		assertThat(body).contains("\"recordsOut\":null", "\"completenessExpected\":null")
				/* 이 둘은 어느 축에도 없는 이름이라 통째 검사가 여전히 맞다 — 데이터셋 축의
				 * 판정 코드는 `unverifiable` 이고 원장 어휘를 그대로 흘리지 않는다. */
				.doesNotContain("freshnessStatus", "freshnessReason");
	}

	@Test
	void 날짜는_그대로_내려가고_생략하면_null_이다() throws Exception {
		MockMvc mvc = mvc(facts());

		mvc.perform(get("/api/v1/console/facts").param("date", "2026-08-01"))
				.andExpect(status().isOk());
		assertThat(repository.requestedDate).isEqualTo(LocalDate.parse("2026-08-01"));

		mvc.perform(get("/api/v1/console/facts")).andExpect(status().isOk());
		assertThat(repository.requestedDate).isNull();
	}

	/** 오타 난 날짜가 아래 계층에서 터지면 500 으로 위장된다 — 게이트에서 400 이다. */
	@Test
	void 날짜_형식이_틀리면_400() throws Exception {
		MockMvc mvc = mvc(facts());

		mvc.perform(get("/api/v1/console/facts").param("date", "2026-8-3"))
				.andExpect(status().isBadRequest());
		mvc.perform(get("/api/v1/console/facts").param("date", "+999999999-12-31"))
				.andExpect(status().isBadRequest());
	}

	/**
	 * 아직 오지 않은 날의 사실은 <b>실측 0 이 아니라 "아직"</b>인데 이 응답에는 그 둘을 가르는
	 * 자리가 없다. 통과시키면 뒤에 붙을 산출 축이 전부 −100% 로 판정돼 거짓 경보가 선다.
	 *
	 * <p>어제는 통과해야 한다 — 상한이 원장의 최신 거래일로 좁아지면 <b>계획이 통째로 안 돈 날</b>
	 * 을 못 열게 되고, 그날이 바로 콘솔이 열려야 하는 날이다.
	 */
	@Test
	void 미래_날짜는_400_이고_지난_날짜는_통과한다() throws Exception {
		MockMvc mvc = mvc(facts());
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
