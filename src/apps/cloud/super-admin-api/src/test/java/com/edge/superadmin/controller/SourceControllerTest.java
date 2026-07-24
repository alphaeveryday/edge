package com.edge.superadmin.controller;

import com.edge.superadmin.mock.SourceMockStore;
import com.edge.superadmin.service.SourceService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui sources 도메인)을 검증한다: 소스 5종과 상태 어휘
 * (COLLECTING·DELAYED)가 화면 그대로 내려오는지가 핵심이다.
 */
class SourceControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new SourceController(new SourceService(new SourceMockStore())))
				.build();
	}

	@Test
	void 수집_리포트는_소스_5종을_반환한다() throws Exception {
		mvc.perform(get("/api/v1/sources/report"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.checkedAt").value("2026-07-12 10:20 KST"))
				.andExpect(jsonPath("$.result.sources.length()").value(5))
				.andExpect(jsonPath("$.result.sources[0].name").value("뉴스"))
				.andExpect(jsonPath("$.result.sources[0].status").value("COLLECTING"))
				.andExpect(jsonPath("$.result.sources[3].status").value("DELAYED"));
	}
}
