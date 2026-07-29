package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.service.AdminSessionService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui session 도메인)을 검증한다: 사이드바·헤더가 쓰는 운영자
 * 컨텍스트는 <b>인증 세션 주체(SessionOperator)</b>를 반영해야 한다 — 종전 mock 의
 * 하드코딩된 고정 프로필이 아니라 로그인한 운영자다(ALPHA-608 실전환의 핵심).
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다.
 */
class AdminSessionControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new AdminSessionController(new AdminSessionService()))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	private MockHttpSession sessionWith(SessionOperator operator) {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionOperator.SESSION_KEY, operator);
		return session;
	}

	@Test
	void 세션은_로그인한_운영자_컨텍스트를_반영한다() throws Exception {
		// 하드코딩(EDGE 운영팀/ops@edge.io)이 아니라 실제 세션 주체가 나와야 한다.
		mvc.perform(get("/api/v1/session")
						.session(sessionWith(new SessionOperator("operator@edge.local", "EDGE 운영팀"))))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.name").value("EDGE 운영팀"))
				.andExpect(jsonPath("$.result.email").value("operator@edge.local"))
				.andExpect(jsonPath("$.result.role").value("Owner"))
				.andExpect(jsonPath("$.result.initials").value("E운"));
	}

	@Test
	void 표시_이름_변경은_같은_세션의_다음_조회에_반영된다() throws Exception {
		MockHttpSession session = sessionWith(new SessionOperator("operator@edge.local", "EDGE 운영팀"));
		mvc.perform(patch("/api/v1/session/profile").session(session)
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"EDGE Ops\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		mvc.perform(get("/api/v1/session").session(session))
				.andExpect(jsonPath("$.result.name").value("EDGE Ops"))
				.andExpect(jsonPath("$.result.email").value("operator@edge.local"))
				.andExpect(jsonPath("$.result.initials").value("EO"));
	}

	@Test
	void 보조평면_유니코드_이름도_이니셜이_깨지지_않는다() throws Exception {
		// 표시 이름은 PATCH 로 임의 유니코드가 들어올 수 있다 — surrogate pair(이모지)를
		// 코드 단위로 쪼개 깨진 대체 문자를 내면 안 된다(코드포인트 경계 절단).
		String emoji = "😀"; // 😀 U+1F600
		mvc.perform(get("/api/v1/session")
						.session(sessionWith(new SessionOperator("operator@edge.local", emoji + " Kim"))))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.initials").value(emoji + "K"));
	}

	@Test
	void 빈_표시_이름은_400이다() throws Exception {
		mvc.perform(patch("/api/v1/session/profile")
						.session(sessionWith(new SessionOperator("operator@edge.local", "EDGE 운영팀")))
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\" \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
	}
}
