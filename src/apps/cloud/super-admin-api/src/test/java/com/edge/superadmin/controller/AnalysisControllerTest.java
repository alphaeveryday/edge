package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.mock.AnalysisMockStore;
import com.edge.superadmin.repository.AnalysisRepository.AnalysisRow;
import com.edge.superadmin.repository.AnalysisRepository.EvidenceRow;
import com.edge.superadmin.service.AnalysisService;
import com.edge.superadmin.support.FakeAnalysisRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.OffsetDateTime;
import java.util.List;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui analyses 도메인) 검증(ALPHA-601): 원장 어휘가 UI 어휘로 번역되고
 * (run_status→status·MIC→시장·수익률→방향/등락률), 결과 없는 런이 상태 문구로 채워지며,
 * 쓰기(정정·제외·복원)는 아직 mock 이라 <b>원장 런 ID 에 404</b> 라는 슬라이스 경계
 * (ALPHA-602 전까지의 사실)를 그대로 드러낸다 — 조용한 성공 둔갑 방지가 핵심이다.
 */
class AnalysisControllerTest {

	/** 하락 −3.42% · 완료 · 근거 2건(뉴스·공시) — 번역 전부를 태우는 행. */
	private static final AnalysisRow COMPLETED_ROW = new AnalysisRow(
			"run-1", "KODEX 반도체", "091160", "XKRX", -0.0342, "SUCCEEDED",
			OffsetDateTime.parse("2026-07-27T15:40:00+09:00"),
			OffsetDateTime.parse("2026-07-27T15:52:00+09:00"),
			"반도체 업황 회복 기대가 확산되며 상승.", "HIGH",
			List.of(new EvidenceRow("NEWS", "반도체 수출 반등", "BIGKINDS",
							OffsetDateTime.parse("2026-07-27T09:10:00+09:00")),
					new EvidenceRow("DISCLOSURE", null, "DART", null)));

	/** 결과 행이 아직 없는 런 — summary·confidence 가 null 로 온다. */
	private static final AnalysisRow PENDING_ROW = new AnalysisRow(
			"run-2", "TIGER 2차전지", "305540", "XKRX", 0.0518, "RUNNING",
			OffsetDateTime.parse("2026-07-27T15:40:00+09:00"), null, null, null, List.of());

	/** SUCCEEDED 인데 본문이 빈 원장 불일치 — 엔진이 "" 를 저장할 수 있다. null 과 동급 결측. */
	private static final AnalysisRow MISMATCH_ROW = new AnalysisRow(
			"run-3", "KODEX 200", "069500", "XKRX", -0.031, "SUCCEEDED",
			OffsetDateTime.parse("2026-07-26T15:40:00+09:00"),
			OffsetDateTime.parse("2026-07-26T15:50:00+09:00"), "", null, List.of());

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new AnalysisController(new AnalysisService(
						new FakeAnalysisRepository(List.of(COMPLETED_ROW, PENDING_ROW, MISMATCH_ROW)),
						new AnalysisMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 목록은_원장_행을_UI_어휘로_번역한다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.length()").value(3))
				.andExpect(jsonPath("$.result[0].id").value("run-1"))
				.andExpect(jsonPath("$.result[0].name").value("KODEX 반도체"))
				.andExpect(jsonPath("$.result[0].market").value("KRX"))
				.andExpect(jsonPath("$.result[0].direction").value(-1))
				.andExpect(jsonPath("$.result[0].changePct").value(3.42))
				.andExpect(jsonPath("$.result[0].status").value("COMPLETED"))
				.andExpect(jsonPath("$.result[0].basisTime").value("07-27 15:40"))
				.andExpect(jsonPath("$.result[0].basisTimeAbs").value("2026-07-27 15:40 KST"))
				.andExpect(jsonPath("$.result[0].doneTime").value("2026-07-27 15:52 KST"))
				.andExpect(jsonPath("$.result[0].confidence").value("HIGH"))
				.andExpect(jsonPath("$.result[0].corrected").value(false))
				.andExpect(jsonPath("$.result[0].evidence.length()").value(2))
				.andExpect(jsonPath("$.result[0].evidence[0].type").value("뉴스"))
				.andExpect(jsonPath("$.result[0].evidence[0].time").value("2026-07-27 09:10"))
				.andExpect(jsonPath("$.result[0].evidence[1].type").value("공시"))
				// 발행시각·제목 없는 공시 — NULL 을 시각처럼 그리지도, UI 계약(title: string)을
				// 깨지도 않는다
				.andExpect(jsonPath("$.result[0].evidence[1].time").value("—"))
				.andExpect(jsonPath("$.result[0].evidence[1].title").value("(제목 없음)"));
	}

	/** SUCCEEDED 런에 본문이 없거나 비면 원장 불일치 — 빈 완료로 숨기지 않는다(Rule 12). */
	@Test
	void 본문_없는_완료_런은_원장_불일치를_드러낸다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$.result[2].status").value("COMPLETED"))
				.andExpect(jsonPath("$.result[2].result")
						.value("설명 본문이 원장에 없습니다 — 완료 런의 explanation_result 가 없거나 비어 있는 원장 불일치입니다."));
	}

	@Test
	void 결과_없는_런은_상태_문구와_빈_완료시각으로_채운다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$.result[1].status").value("PENDING"))
				.andExpect(jsonPath("$.result[1].direction").value(1))
				.andExpect(jsonPath("$.result[1].doneTime").value("—"))
				.andExpect(jsonPath("$.result[1].confidence").doesNotExist())
				.andExpect(jsonPath("$.result[1].result")
						.value("분석 대기 중입니다. 근거 데이터 수집이 완료되면 자동으로 분석이 시작됩니다."));
	}

	/**
	 * 쓰기가 mock 스토어에 남아 있는 동안의 계약 — 실목록의 런 ID 는 mock 에 없으므로 404 다.
	 * 이 테스트는 ALPHA-602(쓰기 전환)에서 <b>깨져야 정상</b>이다(그때 원장 전이로 바뀐다).
	 */
	@Test
	void 원장_런_ID_쓰기는_아직_404다() throws Exception {
		mvc.perform(patch("/api/v1/analyses/run-1/result")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\"정정 시도\"}"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("ADMN4040"));
		mvc.perform(post("/api/v1/analyses/run-1/exclude"))
				.andExpect(status().isNotFound());
		mvc.perform(post("/api/v1/analyses/run-1/restore"))
				.andExpect(status().isNotFound());
	}

	@Test
	void 빈_정정_결과는_400이다() throws Exception {
		mvc.perform(patch("/api/v1/analyses/a1/result")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\" \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
	}
}
