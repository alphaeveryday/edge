package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.mock.AnalysisMockStore;
import com.edge.superadmin.service.AnalysisService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui analyses 도메인)을 검증한다: 정정 = corrected 플래그 동반,
 * 제외/복원 = 제외 전 상태 보존(미완료 건이 COMPLETED 로 둔갑하지 않게), 없는 ID 는
 * 404 로 실패를 드러낸다(성공 토스트 방지)가 핵심이다.
 */
class AnalysisControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new AnalysisController(new AnalysisService(new AnalysisMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 목록은_시안_목데이터_형상을_반환한다() throws Exception {
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.length()").value(12))
				.andExpect(jsonPath("$[0].id").value("a1"))
				.andExpect(jsonPath("$[0].market").value("KRX"))
				.andExpect(jsonPath("$[0].corrected").value(false))
				.andExpect(jsonPath("$[0].evidence.length()").value(3))
				.andExpect(jsonPath("$[11].status").value("EXCLUDED"));
	}

	@Test
	void 정정은_결과와_corrected_플래그를_함께_바꾼다() throws Exception {
		mvc.perform(patch("/api/v1/analyses/a1/result")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\"정정된 분석 결과\"}"))
				.andExpect(status().isNoContent());

		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$[0].result").value("정정된 분석 결과"))
				.andExpect(jsonPath("$[0].corrected").value(true));
	}

	@Test
	void 빈_정정_결과는_400이다() throws Exception {
		mvc.perform(patch("/api/v1/analyses/a1/result")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\" \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
	}

	@Test
	void 제외_복원은_제외_전_상태로_되돌린다() throws Exception {
		// a4 는 PENDING — 복원이 COMPLETED 로 둔갑시키면 미완료 건이 완료로 보인다.
		mvc.perform(post("/api/v1/analyses/a4/exclude"))
				.andExpect(status().isNoContent());
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$[3].status").value("EXCLUDED"));

		mvc.perform(post("/api/v1/analyses/a4/restore"))
				.andExpect(status().isNoContent());
		mvc.perform(get("/api/v1/analyses"))
				.andExpect(jsonPath("$[3].status").value("PENDING"));
	}

	@Test
	void 없는_ID_는_404다() throws Exception {
		mvc.perform(post("/api/v1/analyses/ghost/exclude"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("ADMN4040"));
		mvc.perform(patch("/api/v1/analyses/ghost/result")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"result\":\"x\"}"))
				.andExpect(status().isNotFound());
		mvc.perform(post("/api/v1/analyses/ghost/restore"))
				.andExpect(status().isNotFound());
	}
}
