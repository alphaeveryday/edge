package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.mock.SessionMockStore;
import com.edge.tenantconsole.service.ConsoleSessionService;
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
 * UI 계약(tenant-console-ui session 도메인)을 검증한다: 사이드바·헤더가 쓰는 테넌트
 * 컨텍스트 형상, 표시 이름 변경 반영, 그리고 role 은 mock 이 아니라 인증 주체
 * (SessionMember)의 실 역할로 나가는지가 핵심이다(UI 관리자 가드의 근거).
 */
class ConsoleSessionControllerTest {

	private static final SessionMember REVIEWER =
			new SessionMember(2L, "reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER");

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new ConsoleSessionController(
						new ConsoleSessionService(new SessionMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 세션은_테넌트_컨텍스트와_실_role_을_포함한다() throws Exception {
		mvc.perform(get("/api/v1/session").sessionAttr(SessionMember.SESSION_KEY, REVIEWER))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.name").value("조영서"))
				.andExpect(jsonPath("$.result.tenantName").value("KB증권"))
				.andExpect(jsonPath("$.result.tenantMark").value("KB"))
				// role·email 은 mock 자리표시가 아니라 인증 주체 본인이어야 한다.
				.andExpect(jsonPath("$.result.role").value("COMPLIANCE_REVIEWER"))
				.andExpect(jsonPath("$.result.email").value("reviewer@demo.edge.local"));
	}

	@Test
	void 표시_이름_변경은_다음_조회에_반영된다() throws Exception {
		mvc.perform(patch("/api/v1/session/profile")
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"김영서\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		mvc.perform(get("/api/v1/session").sessionAttr(SessionMember.SESSION_KEY, REVIEWER))
				.andExpect(jsonPath("$.result.name").value("김영서"));
	}

	@Test
	void 빈_표시_이름은_400이다() throws Exception {
		mvc.perform(patch("/api/v1/session/profile")
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\" \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4003"));
	}
}
