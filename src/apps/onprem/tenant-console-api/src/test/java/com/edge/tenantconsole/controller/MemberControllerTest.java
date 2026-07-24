package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.mock.MemberMockStore;
import com.edge.tenantconsole.service.MemberService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(tenant-console-ui users 도메인)을 검증한다: 초대는 INVITED 상태로 목록에
 * 추가되고, 역할 어휘(Admin·Compliance)는 닫혀 있다 — 임의 역할이 통과하면 권한
 * 모델 정합(ALPHA-119)이 시작부터 어긋난다.
 */
class MemberControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new MemberController(new MemberService(new MemberMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 초대는_INVITED_상태로_목록에_추가된다() throws Exception {
		mvc.perform(post("/api/v1/members/invitations")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"new.user@kbsec.com\",\"role\":\"Compliance\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		mvc.perform(get("/api/v1/members"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result[5].email").value("new.user@kbsec.com"))
				.andExpect(jsonPath("$.result[5].name").value("new.user"))
				.andExpect(jsonPath("$.result[5].status").value("INVITED"))
				.andExpect(jsonPath("$.result[5].lastLogin").value("—"));
	}

	@Test
	void 어휘_밖_역할_초대는_400이다() throws Exception {
		mvc.perform(post("/api/v1/members/invitations")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"x@kbsec.com\",\"role\":\"Owner\"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4003"));
	}
}
