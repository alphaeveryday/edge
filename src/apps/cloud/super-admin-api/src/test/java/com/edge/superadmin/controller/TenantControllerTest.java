package com.edge.superadmin.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.superadmin.entity.Tenant;
import com.edge.superadmin.service.TenantService;
import com.edge.superadmin.support.FakeTenantRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.OffsetDateTime;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(super-admin-ui tenants 도메인)을 검증한다: 목록은 최신순(id desc)으로 와이어
 * 형상(env 표기 Prod/Dev, tenant 테이블 미보유 필드는 플레이스홀더)을 내고, 생성은 검증 후
 * ONBOARDING 으로 저장돼 목록 맨 앞에 나타난다(콘솔 IA — 연결 상태는 Sync 채널 기준)가 핵심.
 * standalone 셋업은 in-memory 페이크 리포지토리를 쓴다(실 스키마 왕복은 TenantRepositoryTest).
 */
class TenantControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		FakeTenantRepository repository = new FakeTenantRepository(
				new Tenant(1L, "미래에셋증권", "PROD", "ACTIVE", OffsetDateTime.now()),
				new Tenant(2L, "한국투자증권", "PROD", "SYNC_DELAYED", OffsetDateTime.now()),
				new Tenant(3L, "키움증권", "PROD", "ACTIVE", OffsetDateTime.now()));
		mvc = MockMvcBuilders
				.standaloneSetup(new TenantController(new TenantService(repository)))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 목록은_최신순_테넌트_와이어_형상을_반환한다() throws Exception {
		mvc.perform(get("/api/v1/tenants"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.length()").value(3))
				// 최신(id desc)순 — 마지막 시드(id 3)가 맨 앞
				.andExpect(jsonPath("$.result[0].id").value("3"))
				.andExpect(jsonPath("$.result[0].name").value("키움증권"))
				.andExpect(jsonPath("$.result[0].env").value("Prod")) // PROD→Prod 경계 변환
				.andExpect(jsonPath("$.result[0].status").value("ACTIVE"))
				.andExpect(jsonPath("$.result[1].status").value("SYNC_DELAYED"))
				// tenant 테이블 미보유 필드는 플레이스홀더(스키마 확장 시 복원)
				.andExpect(jsonPath("$.result[0].domain").value(""))
				.andExpect(jsonPath("$.result[0].calls").value(0))
				.andExpect(jsonPath("$.result[0].bars.length()").value(24));
	}

	@Test
	void 생성은_ONBOARDING_으로_저장돼_최신순_맨_앞에_나온다() throws Exception {
		mvc.perform(post("/api/v1/tenants")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\"대신증권\",\"env\":\"Dev\",\"admin\":\"홍길동\","
								+ "\"email\":\"gd.hong@daishin.com\",\"memo\":\"PoC\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		mvc.perform(get("/api/v1/tenants"))
				.andExpect(jsonPath("$.result.length()").value(4))
				.andExpect(jsonPath("$.result[0].name").value("대신증권"))
				.andExpect(jsonPath("$.result[0].status").value("ONBOARDING"))
				.andExpect(jsonPath("$.result[0].env").value("Dev")); // Dev→DEV→Dev 왕복
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
