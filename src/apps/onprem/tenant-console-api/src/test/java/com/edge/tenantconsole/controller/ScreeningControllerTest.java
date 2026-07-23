package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.mock.ScreeningMockStore;
import com.edge.tenantconsole.service.ScreeningService;
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
 * UI 계약(tenant-console-ui screening 도메인)을 검증한다: 금칙어·자동 제공 기준·면책
 * 문구의 어휘 게이트(risk·action·maxRisk)와 PATCH 부분 갱신 시맨틱이 핵심이다 —
 * 어휘 밖 값이 통과하면 점검 정책 자체가 오염된다.
 */
class ScreeningControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new ScreeningController(
						new ScreeningService(new ScreeningMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 금칙어_등록은_맨_위에_활성으로_추가된다() throws Exception {
		// WHY: UI 목록은 최신 등록이 맨 위(구 mock 과 동일 정렬 계약).
		mvc.perform(post("/api/v1/screening/words")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"원금 보장\",\"risk\":\"HIGH\",\"action\":\"BLOCK\"}"))
				.andExpect(status().isNoContent());

		mvc.perform(get("/api/v1/screening/words"))
				.andExpect(jsonPath("$[0].text").value("원금 보장"))
				.andExpect(jsonPath("$[0].active").value(true))
				.andExpect(jsonPath("$[0].id").value(7));
	}

	@Test
	void 어휘_밖_금칙어_등록은_400이다() throws Exception {
		// WHY: risk·action 은 닫힌 어휘 — 임의 값이 들어오면 점검 분기가 무의미해진다.
		mvc.perform(post("/api/v1/screening/words")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"x\",\"risk\":\"EXTREME\",\"action\":\"BLOCK\"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4003"));
	}

	@Test
	void 토글은_활성_여부를_뒤집는다() throws Exception {
		mvc.perform(post("/api/v1/screening/words/1/toggle"))
				.andExpect(status().isNoContent());
		mvc.perform(get("/api/v1/screening/words"))
				.andExpect(jsonPath("$[0].id").value(1))
				.andExpect(jsonPath("$[0].active").value(false));

		mvc.perform(post("/api/v1/screening/words/999/toggle"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4042"));
	}

	@Test
	void 기준_부분_갱신은_나머지_필드를_유지한다() throws Exception {
		// WHY: PATCH 시맨틱 — minSources 만 보내는 UI 흐름에서 maxRisk 가 초기화되면 안 된다.
		mvc.perform(patch("/api/v1/screening/criteria")
						.contentType(MediaType.APPLICATION_JSON).content("{\"minSources\":3}"))
				.andExpect(status().isNoContent());
		mvc.perform(get("/api/v1/screening/criteria"))
				.andExpect(jsonPath("$.minSources").value(3))
				.andExpect(jsonPath("$.maxRisk").value("MEDIUM"));
	}

	@Test
	void 자동_제공_상한에_HIGH_는_없다() throws Exception {
		// WHY: HIGH 는 항상 검수·차단 경로 — 상한으로 허용되면 자동 제공 게이트가 뚫린다.
		mvc.perform(patch("/api/v1/screening/criteria")
						.contentType(MediaType.APPLICATION_JSON).content("{\"maxRisk\":\"HIGH\"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4003"));
	}

	@Test
	void 면책_문구는_text_객체로_감싼다() throws Exception {
		// WHY: 원시 문자열 응답은 apiClient 의 JSON 파싱 계약과 어긋난다 — {text} 로 고정.
		mvc.perform(patch("/api/v1/screening/disclaimer")
						.contentType(MediaType.APPLICATION_JSON).content("{\"text\":\"새 면책 문구\"}"))
				.andExpect(status().isNoContent());
		mvc.perform(get("/api/v1/screening/disclaimer"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.text").value("새 면책 문구"));
	}
}
