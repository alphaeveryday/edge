package com.edge.superadmin.auth;

import com.edge.superadmin.controller.TenantController;
import com.edge.superadmin.mock.TenantMockStore;
import com.edge.superadmin.service.TenantService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 인증 계약(ALPHA-474 앱 방어선)을 검증한다: 미인증 = 전 표면 401(fail-closed),
 * 매핑 없는 표면 = 403(fail-closed), 로그인만 공개. RULES 가 super-admin-console.md
 * 화면 표면과 1:1 이라는 전제가 이 테스트의 WHY 다.
 */
class AdminAuthFilterTest {

	private static final SessionOperator OPERATOR =
			new SessionOperator("operator@edge.local", "EDGE 운영팀");

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new TenantController(new TenantService(new TenantMockStore())))
				.addFilters(new AdminAuthFilter())
				.build();
	}

	private MockHttpSession sessionOf(SessionOperator operator) {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionOperator.SESSION_KEY, operator);
		return session;
	}

	@Test
	void 미인증_요청은_전_표면에서_401이다() throws Exception {
		mvc.perform(get("/api/v1/tenants"))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.isSuccess").value(false))
				.andExpect(jsonPath("$.code").value("ADMN4011"));
		mvc.perform(post("/api/v1/tenants"))
				.andExpect(status().isUnauthorized());
	}

	@Test
	void 인증된_운영자는_매핑된_표면에_접근한다() throws Exception {
		mvc.perform(get("/api/v1/tenants").session(sessionOf(OPERATOR)))
				.andExpect(status().isOk());
	}

	@Test
	void 매핑_없는_admin_표면은_인증돼도_403이다() throws Exception {
		// RULES 에 행이 없는 표면은 거부가 기본(fail-closed) —
		// 새 엔드포인트가 매핑 없이 배포되는 것을 구조적으로 막는다.
		mvc.perform(get("/api/v1/unknown").session(sessionOf(OPERATOR)))
				.andExpect(status().isForbidden())
				.andExpect(jsonPath("$.code").value("ADMN4030"));
	}

	@Test
	void matrix_parameter_우회는_인증을_건너뛰지_못한다() throws Exception {
		// `/api;x=y/...` 는 MVC 가 매트릭스 파라미터를 벗겨 admin API 로 매핑한다 —
		// 필터도 같은 정규화를 적용해 미인증 요청을 401 로 막아야 한다(fail-closed).
		mvc.perform(post("/api;x=y/v1/tenants"))
				.andExpect(status().isUnauthorized());
	}

	@Test
	void 인코딩된_matrix_구분자_우회도_차단된다() throws Exception {
		// `%3B`(인코딩된 ;)는 MVC 가 디코딩 후 매핑하므로, 필터도 디코딩 후 판정해야
		// `/api%3Bx=y/...` 우회를 막는다. requestURI 를 직접 지정해 인코딩을 보존한다.
		mvc.perform(post("/x")
						.with(request -> {
							request.setRequestURI("/api%3Bx=y/v1/tenants");
							request.setServletPath("/api%3Bx=y/v1/tenants");
							return request;
						}))
				.andExpect(status().isUnauthorized());
	}

	@Test
	void 로그인은_유일한_공개_표면이다() throws Exception {
		// 필터를 통과해 MVC 까지 도달한다 — 이 셋업엔 AuthController 가 없어 404 가
		// 곧 "차단되지 않았다"의 증거다(401/403 이면 필터가 막은 것).
		mvc.perform(post("/api/v1/auth/login"))
				.andExpect(status().isNotFound());
	}

	@Test
	void actuator_는_필터_관심사가_아니다() throws Exception {
		// /api/ 밖 경로는 통과 — 이 셋업엔 핸들러가 없어 404 가 통과의 증거다.
		mvc.perform(get("/actuator/health"))
				.andExpect(status().isNotFound());
	}
}
