package com.edge.superadmin.controller;

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

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui sources 도메인) 검증 — 원장 4축 어휘가 뭉개지지 않고 그대로 내려오는지가
 * 핵심이다(ALPHA-514).
 */
class SourceControllerTest {

	private static final OffsetDateTime FINISHED =
			OffsetDateTime.of(2026, 7, 27, 6, 40, 0, 0, ZoneOffset.UTC);

	private MockMvc mvc(PipelineRunStatus run) {
		return MockMvcBuilders
				.standaloneSetup(new SourceController(
						new SourceService(new FakePipelineStatusRepository(run))))
				.build();
	}

	/**
	 * 픽스처는 운영에서 실제로 나오는 이종을 섞는다 — 정상 작업·SKIPPED 작업·봉투를 못 낸 작업.
	 * 셋 다 dev 최신 런에 실재한다(21 FULFILLED · 3 SKIPPED · 카운터 전부 NULL). 동종만 넣으면
	 * null 경로를 아예 안 밟아 결함이 초록으로 통과한다.
	 */
	private static PipelineRunStatus sampleRun() {
		return new PipelineRunStatus("etf-daily:2026-07-27T15:40", "LAUNCHED", "FAILED",
				LocalDate.of(2026, 7, 27), List.of(
				new TaskStatus("raw", "PRICE_COLLECTION_KIS", "price_daily", "DUE",
						"FULFILLED", "VALID", 2736L, 0L, FINISHED),
				new TaskStatus("raw", "NEWS_COLLECTION_BIGKINDS", "stock_news", "SKIPPED",
						null, null, null, null, null),
				// 실행은 성공인데 데이터는 불완전 — 두 축이 따로 내려가는지 잠근다.
				new TaskStatus("feature", "TAG_NEWS", "news_assertions", "DUE",
						"FULFILLED", "INCOMPLETE", null, null, FINISHED)));
	}

	@Test
	void 최신_런의_런헤더와_작업목록을_그대로_낸다() throws Exception {
		mvc(sampleRun()).perform(get("/api/v1/sources/report"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.run.runKey").value("etf-daily:2026-07-27T15:40"))
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
				.andExpect(jsonPath("$.result.tasks[2].failedRecords").doesNotExist());
	}

	@Test
	void 계획에서_빠진_작업은_outcome_없이_SKIPPED_로_내려간다() throws Exception {
		// WHY: SKIPPED(휴장이라 안 함)를 FULFILLED(해서 됐음)와 같은 초록으로 뭉개면 운영자가
		//      "오늘 수집이 돌긴 했나"에 답할 수 없다. plan 축과 outcome 축을 분리해 내린다.
		mvc(sampleRun()).perform(get("/api/v1/sources/report"))
				.andExpect(jsonPath("$.result.tasks[1].planStatus").value("SKIPPED"))
				.andExpect(jsonPath("$.result.tasks[1].outcome").doesNotExist())
				.andExpect(jsonPath("$.result.tasks[1].lastFinishedAt").doesNotExist());
	}

	@Test
	void 원장에_런이_없으면_에러가_아니라_빈_리포트다() throws Exception {
		// WHY: 초기 환경·원장 미가동은 장애가 아니다. 여기서 500 을 내면 콘솔 페이지가 통째로
		//      안 뜬다 — 볼 게 없는 것과 고장 난 것은 다르다.
		mvc(null).perform(get("/api/v1/sources/report"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.run").doesNotExist())
				.andExpect(jsonPath("$.result.tasks.length()").value(0));
	}
}
