package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.auth.BootstrapOperators;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.service.AuthService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 인증 계약(config 부트스트랩 운영자)을 검증한다: 로그인 성공 = 운영자 실린 세션,
 * 실패 사유는 구분 없는 401(계정 존재 여부 비노출), 로그아웃 = 세션 무효화.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 */
class AuthControllerTest {

	private static final String PASSWORD = "demo-operator-1";

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		AuthService authService = new AuthService(new BootstrapOperators(List.of(
				new BootstrapOperators.Operator("operator@edge.local", "EDGE 운영팀", PASSWORD))));
		mvc = MockMvcBuilders.standaloneSetup(new AuthController(authService))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 로그인_성공은_운영자가_실린_세션을_만든다() throws Exception {
		MvcResult result = mvc.perform(post("/api/v1/auth/login")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"operator@edge.local\",\"password\":\"" + PASSWORD + "\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.email").value("operator@edge.local"))
				.andExpect(jsonPath("$.result.name").value("EDGE 운영팀"))
				.andReturn();

		Object attribute = result.getRequest().getSession(false)
				.getAttribute(SessionOperator.SESSION_KEY);
		assertThat(attribute).isInstanceOf(SessionOperator.class);
	}

	@Test
	void 이메일은_대소문자_공백_무관하게_같은_계정이다() throws Exception {
		mvc.perform(post("/api/v1/auth/login")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\" Operator@Edge.Local \",\"password\":\"" + PASSWORD + "\"}"))
				.andExpect(status().isOk());
	}

	@Test
	void 로그인_실패는_사유_구분_없는_401이다() throws Exception {
		// 미존재 이메일과 비밀번호 불일치가 같은 코드 — 계정 존재 여부 비노출.
		mvc.perform(post("/api/v1/auth/login")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"ghost@edge.local\",\"password\":\"nope\"}"))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.code").value("ADMN4010"));
		mvc.perform(post("/api/v1/auth/login")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"operator@edge.local\",\"password\":\"wrong\"}"))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.code").value("ADMN4010"));
	}

	@Test
	void 빈_요청_본문도_같은_401이다() throws Exception {
		mvc.perform(post("/api/v1/auth/login"))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.code").value("ADMN4010"));
	}

	@Test
	void 로그아웃은_세션을_무효화한다() throws Exception {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionOperator.SESSION_KEY,
				new SessionOperator("operator@edge.local", "EDGE 운영팀"));

		mvc.perform(post("/api/v1/auth/logout").session(session))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		assertThat(session.isInvalid()).isTrue();
	}

	@Test
	void 세션_조회는_로그인한_운영자를_반환한다() throws Exception {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionOperator.SESSION_KEY,
				new SessionOperator("operator@edge.local", "EDGE 운영팀"));

		mvc.perform(get("/api/v1/auth/session").session(session))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.result.email").value("operator@edge.local"));
	}
}
