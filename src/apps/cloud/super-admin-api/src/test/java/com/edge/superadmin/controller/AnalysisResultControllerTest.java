package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.repository.AnalysisResultRepository;
import com.edge.superadmin.service.AnalysisResultService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.nullValue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 새 분석 결과 표면(ALPHA-1333)의 와이어 계약 — UI 타입(`domains/analysisResults/types.ts`)과 같은
 * camelCase 키, 요청 값이 저장소까지 <b>그대로</b> 내려가는지(날짜를 KST 하루 경계로 바꾸는 것만 예외),
 * 틀린 값은 400·없는 분석은 404. 조회 SQL 은 JdbcAnalysisResultRepositoryIntegrationTest 소관이다.
 */
class AnalysisResultControllerTest {

	private static final OffsetDateTime AT = OffsetDateTime.parse("2026-10-13T09:00:00+09:00");

	private final List<AnalysisResultRepository.ListQuery> seen = new ArrayList<>();
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		AnalysisResultRepository fake = new AnalysisResultRepository() {
			@Override
			public Page list(ListQuery query) {
				seen.add(query);
				return new Page(51, query.page(), query.size(), List.of(new ListRow("o-1", "069500",
						"KODEX 200", AT, "failed", "database", false, null, null, null, "제목", "오류 앞")));
			}

			@Override
			public Detail detail(Kind kind, String analysisId) {
				if (!analysisId.equals("m-1") || kind != Kind.MOVEMENT) {
					return null;
				}
				return new Detail("m-1", "movement", "069500", "KODEX 200", AT, "completed", "database",
						true, AT, null, null, null, new Movement(AT.toLocalDate(), "요약",
						List.of("i-2"), false, null, List.of(
								new MovementItem("i-1", "이슈", "키", "문장", "positive", null, List.of()),
								new MovementItem("i-2", "차트", "키", "문장", "negative", null, List.of("r")))));
			}
		};
		mvc = MockMvcBuilders
				.standaloneSetup(new AnalysisResultController(new AnalysisResultService(fake)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 목록은_필터를_그대로_넘기고_날짜는_KST_하루_경계로_바꾼다() throws Exception {
		mvc.perform(get("/api/v1/analysis-results?kind=movement&from=2026-10-12&to=2026-10-13"
						+ "&status=failed&page=1&size=20").param("etf", " 069500 "))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.total").value(51))
				.andExpect(jsonPath("$.result.rows[0].analysisId").value("o-1"))
				.andExpect(jsonPath("$.result.rows[0].etfName").value("KODEX 200"))
				.andExpect(jsonPath("$.result.rows[0].published").value(false))
				/* 전망에는 회수 열이 없다 — 키는 있고 값이 null 이다(false 면 "회수 안 됨"이라는 거짓말) */
				.andExpect(jsonPath("$.result.rows[0].withdrawn").value(nullValue()))
				.andExpect(jsonPath("$.result.rows[0].errorHead").value("오류 앞"));
		var q = seen.get(0);
		assertThat(q.kind()).isEqualTo(AnalysisResultRepository.Kind.MOVEMENT);
		assertThat(q.from()).isEqualTo(OffsetDateTime.parse("2026-10-12T00:00:00+09:00"));
		/* 종료일 포함 — 상한은 다음 날 00:00 KST(제외) */
		assertThat(q.to()).isEqualTo(OffsetDateTime.parse("2026-10-14T00:00:00+09:00"));
		assertThat(q.etf()).isEqualTo("069500");
		assertThat(q.status()).isEqualTo("failed");
		assertThat(q.allSources()).as("기본은 운영 산출만").isFalse();
		assertThat(List.of(q.page(), q.size())).containsExactly(1, 20);
	}

	@Test
	void 기간을_안_주면_KST_오늘_하루다() throws Exception {
		LocalDate before = LocalDate.now(ZoneId.of("Asia/Seoul"));
		mvc.perform(get("/api/v1/analysis-results?kind=outlook&allSources=true")).andExpect(status().isOk());
		LocalDate after = LocalDate.now(ZoneId.of("Asia/Seoul"));
		var q = seen.get(0);
		/* 날짜 자체가 KST 오늘이다 — 자정을 넘기는 순간에 돌면 앞뒤 어느 날이든 받는다 */
		assertThat(q.from()).isIn(before.atStartOfDay(ZoneId.of("Asia/Seoul")).toOffsetDateTime(),
				after.atStartOfDay(ZoneId.of("Asia/Seoul")).toOffsetDateTime());
		assertThat(q.to()).isEqualTo(q.from().plusDays(1));
		assertThat(q.etf()).isNull();
		assertThat(q.allSources()).isTrue();
	}

	@Test
	void 시작일만_주면_그날_하루다() throws Exception {
		mvc.perform(get("/api/v1/analysis-results?kind=outlook&from=2026-10-01")).andExpect(status().isOk());
		var q = seen.get(0);
		assertThat(q.from()).isEqualTo(OffsetDateTime.parse("2026-10-01T00:00:00+09:00"));
		assertThat(q.to()).as("오늘까지가 아니다").isEqualTo(OffsetDateTime.parse("2026-10-02T00:00:00+09:00"));
	}

	@Test
	void 틀린_요청은_400이고_저장소까지_가지_않는다() throws Exception {
		for (String query : List.of("", "kind=etf", "kind=outlook&from=2026-13-01",
				"kind=outlook&from=2026-10-13&to=2026-10-12", "kind=outlook&status=withdrawn",
				"kind=outlook&size=201", "kind=outlook&size=0", "kind=outlook&page=-1")) {
			mvc.perform(get("/api/v1/analysis-results?" + query))
					.andExpect(status().isBadRequest())
					.andExpect(jsonPath("$.code").value("ADMN4001"));
		}
		assertThat(seen).isEmpty();
	}

	@Test
	void 상세는_종류_갈래_하나만_싣고_없으면_404다() throws Exception {
		mvc.perform(get("/api/v1/analysis-results/movement/m-1"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.kind").value("movement"))
				.andExpect(jsonPath("$.result.outlook").value(nullValue()))
				.andExpect(jsonPath("$.result.movement.selectedItemIds[0]").value("i-2"))
				.andExpect(jsonPath("$.result.movement.items.length()").value(2))
				.andExpect(jsonPath("$.result.movement.items[1].toolRunIds[0]").value("r"));
		mvc.perform(get("/api/v1/analysis-results/outlook/m-1")).andExpect(status().isNotFound());
		mvc.perform(get("/api/v1/analysis-results/etf/m-1")).andExpect(status().isBadRequest());
	}
}
