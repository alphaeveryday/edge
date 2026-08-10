package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.repository.ConsoleFactsRepository.ConsoleFacts;
import com.edge.superadmin.repository.ConsoleFactsRepository.RunRow;
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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 콘솔 사실 응답의 <b>조회 창 + 런 축</b> 계약(ALPHA-738).
 *
 * <p>지키는 것 셋 — ① 요청한 날이 <b>그대로 아래로 내려가는가</b>(게이트가 값을 조용히 바꾸면
 * 화면은 다른 날을 보고도 모른다) ② 원장이 <b>실제로 무엇을 봤는지</b> 되돌려주는가 ③ 런 축이
 * <b>원장 값 그대로</b> 나가는가(어휘를 다시 정의하면 판정이 서버로 샌다).
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
		return new ConsoleFacts(DAY, DB_NOW, List.of(runs));
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
		assertThat(body).doesNotContain("\"tasks\"", "\"datasets\"", "\"outputs\"", "\"boundary\"");
		/* 🔴 **셋째 다리**: 런이 0건인 날의 `runs: []` 는 **사실**이라 키가 있어야 한다("봤는데
		 * 없었다"). 이걸 안 재면 `NON_EMPTY` 한 줄에 키가 통째로 사라져 규칙 층이 "아직 안 봄"
		 * 으로 읽는데 전건 초록이다 — 부재 3분 중 이 다리만 비어 있었다. */
		assertThat(body).contains("\"runs\":[]");
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
						DB_NOW, DB_NOW.plusHours(1)),
				new RunRow("news:2026-08-03T15:30", "news", DAY, "RUNNING", DB_NOW, null)))
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
				DB_NOW, null)))
				.perform(get("/api/v1/console/facts"))
				.andExpect(status().isOk())
				.andReturn().getResponse().getContentAsString();

		/* `jsonPath(...).doesNotExist()` 는 null 도 통과시켜 "키 있고 null" 과 "키 없음" 을 못
		 * 가른다 — 문자열로 키의 실재를 확인한다. */
		assertThat(body).contains("\"tradingDate\":null", "\"deadline\":null");
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
