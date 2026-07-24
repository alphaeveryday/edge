package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.mock.ScopeMockStore;
import com.edge.tenantconsole.service.ScopeService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(tenant-console-ui scope 도메인)을 검증한다: 시장 카드의 종목 수는 종목
 * 목록에서 집계된 값이어야 하고(별도 카운터가 어긋나면 화면 불일치), 토글 대상이
 * 없으면 404 로 드러난다.
 */
class ScopeControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		mvc = MockMvcBuilders
				.standaloneSetup(new ScopeController(new ScopeService(new ScopeMockStore())))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 시장_목록은_종목_수를_집계해_반환한다() throws Exception {
		mvc.perform(get("/api/v1/scope/markets"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result[0].market").value("KRX"))
				.andExpect(jsonPath("$.result[0].stockCount").value(8))
				.andExpect(jsonPath("$.result[1].market").value("NASDAQ"))
				.andExpect(jsonPath("$.result[1].stockCount").value(5));
	}

	@Test
	void 시장_토글은_제공_여부를_뒤집는다() throws Exception {
		mvc.perform(post("/api/v1/scope/markets/KRX/toggle"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		mvc.perform(get("/api/v1/scope/markets"))
				.andExpect(jsonPath("$.result[0].enabled").value(false));
	}

	@Test
	void 없는_시장_토글은_404다() throws Exception {
		// WHY: 시장 어휘는 KRX·NASDAQ 뿐 — 임의 경로 값이 새 시장을 만들면 안 된다.
		mvc.perform(post("/api/v1/scope/markets/NYSE/toggle"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4043"));
	}

	@Test
	void 종목_토글은_해당_종목만_바꾼다() throws Exception {
		mvc.perform(post("/api/v1/scope/stocks/TSLA/toggle"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		mvc.perform(get("/api/v1/scope/stocks"))
				.andExpect(jsonPath("$.result[5].code").value("TSLA"))
				.andExpect(jsonPath("$.result[5].enabled").value(false))
				.andExpect(jsonPath("$.result[0].enabled").value(true));
	}
}
