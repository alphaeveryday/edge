package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.mock.ExplanationMockStore;
import com.edge.tenantconsole.service.ExplanationService;
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
 * UI 계약(tenant-console-ui explanations 도메인)을 검증한다: 필드명은 camelCase 그대로,
 * `final` 필드가 예약어 우회(finalText)로 이름이 바뀌면 화면 렌더링이 통째로 깨진다.
 * 상태 전이는 state-machine 어휘(APPROVED·UNPUBLISHED 등)를 따른다.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 */
class ExplanationControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new ExplanationController(
						new ExplanationService(new ExplanationMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 목록은_UI_계약_형상이다() throws Exception {
		// WHY: UI 는 이 필드명(camelCase·"final")으로 그대로 렌더링한다 — 직렬화가
		// finalText/snake_case 로 새면 mock→real 전환 시 화면이 깨진다.
		mvc.perform(get("/api/v1/explanations"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$[0].name").value("삼성전자"))
				.andExpect(jsonPath("$[0].changePct").value(3.24))
				.andExpect(jsonPath("$[0].final").exists())
				.andExpect(jsonPath("$[0].finalText").doesNotExist())
				// 검수 사유는 해당 상태에서만 존재한다(UI optional 필드 계약)
				.andExpect(jsonPath("$[0].reviewReason").doesNotExist())
				.andExpect(jsonPath("$[1].reviewReason").value("ASSERTIVE"))
				.andExpect(jsonPath("$[0].evidence[0].type").value("공시"));
	}

	@Test
	void 승인은_APPROVED_전이와_최종_문구_반영이_한_단위다() throws Exception {
		// WHY: 검수자 승인은 자동 제공과 구분되는 APPROVED 이고, 승인 문구가 곧
		// 노출 문면이 된다 — 전이만 되고 문구가 옛것이면 검수의 의미가 없다.
		mvc.perform(post("/api/v1/explanations/2/approve")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"final\":\"검수 반영 문구\",\"note\":\"단정 표현 제거\"}"))
				.andExpect(status().isNoContent());

		mvc.perform(get("/api/v1/explanations"))
				.andExpect(jsonPath("$[1].status").value("APPROVED"))
				.andExpect(jsonPath("$[1].final").value("검수 반영 문구"))
				.andExpect(jsonPath("$[1].reviewReason").doesNotExist());
	}

	@Test
	void 반려는_사유가_필수다() throws Exception {
		// WHY: 반려 사유는 감사 재현의 최소 단서(state-machine.md) — 빈 사유 통과 금지.
		mvc.perform(post("/api/v1/explanations/2/reject")
						.contentType(MediaType.APPLICATION_JSON).content("{\"note\":\"  \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4001"));
	}

	@Test
	void 없는_설명에_대한_액션은_404다() throws Exception {
		// WHY: 구 UI mock 과 같은 계약 — 없는 ID 에 성공 응답이 나가면 성공 토스트가 뜬다.
		mvc.perform(post("/api/v1/explanations/999/stop"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4041"));
	}

	@Test
	void 최종_문구는_빈_값을_거부하고_임시_저장은_허용한다() throws Exception {
		// WHY: 최종 문구는 고객 노출 문면(빈 값 금지), 임시 저장은 작성 중간 상태다.
		mvc.perform(patch("/api/v1/explanations/1/final")
						.contentType(MediaType.APPLICATION_JSON).content("{\"final\":\"\"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4003"));
		mvc.perform(patch("/api/v1/explanations/1/draft")
						.contentType(MediaType.APPLICATION_JSON).content("{\"final\":\"\"}"))
				.andExpect(status().isNoContent());
	}

	@Test
	void 반입_상태는_UI_계약_형상이다() throws Exception {
		mvc.perform(get("/api/v1/explanations/feed-status"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.state").value("NORMAL"))
				.andExpect(jsonPath("$.todayReceived").value(128));
	}
}
