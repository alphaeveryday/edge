package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.mock.TenantMockStore;
import com.edge.superadmin.service.TenantService;
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
 * UI 계약(super-admin-ui tenants 도메인)을 검증한다: 목록 형상(bars 24칸 포함)과
 * 생성 시 온보딩 상태 시작(콘솔 IA — 연결 상태는 Sync 채널 기준)이 핵심이다.
 */
class TenantControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new TenantController(new TenantService(new TenantMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 목록은_시안_목데이터_형상을_반환한다() throws Exception {
		mvc.perform(get("/api/v1/tenants"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.length()").value(8))
				.andExpect(jsonPath("$[0].id").value("t1"))
				.andExpect(jsonPath("$[0].name").value("미래에셋증권"))
				.andExpect(jsonPath("$[0].status").value("ACTIVE"))
				.andExpect(jsonPath("$[0].bars.length()").value(24))
				.andExpect(jsonPath("$[2].status").value("SYNC_DELAYED"));
	}

	@Test
	void 생성은_목록_맨_앞에_온보딩_상태로_추가된다() throws Exception {
		mvc.perform(post("/api/v1/tenants")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\"대신증권\",\"env\":\"Dev\",\"admin\":\"홍길동\","
								+ "\"email\":\"gd.hong@daishin.com\",\"memo\":\"PoC\"}"))
				.andExpect(status().isNoContent());

		mvc.perform(get("/api/v1/tenants"))
				.andExpect(jsonPath("$.length()").value(9))
				.andExpect(jsonPath("$[0].name").value("대신증권"))
				.andExpect(jsonPath("$[0].status").value("ONBOARDING"))
				.andExpect(jsonPath("$[0].domain").value("daishin.com"))
				.andExpect(jsonPath("$[0].lastSync").value("—"));
	}

	@Test
	void 지원하지_않는_env_는_400이다() throws Exception {
		mvc.perform(post("/api/v1/tenants")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\"대신증권\",\"env\":\"Staging\",\"admin\":\"홍길동\","
								+ "\"email\":\"gd.hong@daishin.com\"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("ADMN4001"));
	}

	@Test
	void 필수_필드_누락은_400이다() throws Exception {
		mvc.perform(post("/api/v1/tenants")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\" \",\"env\":\"Dev\",\"admin\":\"홍길동\","
								+ "\"email\":\"gd.hong@daishin.com\"}"))
				.andExpect(status().isBadRequest());
		mvc.perform(post("/api/v1/tenants"))
				.andExpect(status().isBadRequest());
	}
}
