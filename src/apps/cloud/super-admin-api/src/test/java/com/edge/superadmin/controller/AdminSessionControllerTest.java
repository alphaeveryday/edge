package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.mock.AdminSessionMockStore;
import com.edge.superadmin.service.AdminSessionService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui session 도메인)을 검증한다: 사이드바·헤더가 쓰는 운영자
 * 컨텍스트 형상과 표시 이름 변경 반영이 핵심이다.
 */
class AdminSessionControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new AdminSessionController(
						new AdminSessionService(new AdminSessionMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 세션은_운영자_컨텍스트를_포함한다() throws Exception {
		mvc.perform(get("/api/v1/session"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.name").value("EDGE 운영팀"))
				.andExpect(jsonPath("$.role").value("Owner"))
				.andExpect(jsonPath("$.initials").value("OP"));
	}

	@Test
	void 표시_이름_변경은_다음_조회에_반영된다() throws Exception {
		mvc.perform(patch("/api/v1/session/profile")
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"EDGE Ops\"}"))
				.andExpect(status().isNoContent());
		mvc.perform(get("/api/v1/session"))
				.andExpect(jsonPath("$.name").value("EDGE Ops"));
	}

	@Test
	void 빈_표시_이름은_400이다() throws Exception {
		mvc.perform(patch("/api/v1/session/profile")
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\" \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
	}
}
