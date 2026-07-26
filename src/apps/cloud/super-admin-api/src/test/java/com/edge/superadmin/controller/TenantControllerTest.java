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
 * UI 계약(super-admin-ui tenants 도메인)을 검증한다(ALPHA-121): 목록은 최신순(id desc)
 * 와이어 형상(env 표기 = IA 어휘 PoC/Production, 레거시 DEV 는 수축 전 Dev 로), 생성은
 * 검증 후 ONBOARDING("미연결" — 연결 상태는 Sync 채널 기준)으로 저장돼 목록 맨 앞에
 * 나타나며, 생성 폼 필드(초기 admin·메모)가 보존돼 응답에 실린다.
 * standalone 셋업은 in-memory 페이크 리포지토리를 쓴다(실 스키마 왕복은 IT).
 */
class TenantControllerTest {

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		FakeTenantRepository repository = new FakeTenantRepository(
				new Tenant(1L, "미래에셋증권", "PROD", "ACTIVE", "김미래", "kim@mirae.com",
						null, OffsetDateTime.now()),
				new Tenant(2L, "한국투자증권", "DEV", "SYNC_DELAYED", null, null,
						null, OffsetDateTime.now()),
				new Tenant(3L, "키움증권", "PROD", "ACTIVE", "박키움", "park@kiwoom.com",
						"PoC 협의 중", OffsetDateTime.now()));
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
				.andExpect(jsonPath("$.result[0].env").value("Production")) // PROD→Production(IA 어휘)
				.andExpect(jsonPath("$.result[0].status").value("ACTIVE"))
				// 보존된 온보딩 기록이 응답에 실린다(플레이스홀더가 아니라 원장 값)
				.andExpect(jsonPath("$.result[0].admin").value("박키움"))
				.andExpect(jsonPath("$.result[0].email").value("park@kiwoom.com"))
				.andExpect(jsonPath("$.result[0].memo").value("PoC 협의 중"))
				// 레거시 DEV 행(수축 전)은 Dev 로 — 확장 전 데이터가 깨져 보이지 않는다
				.andExpect(jsonPath("$.result[1].env").value("Dev"))
				.andExpect(jsonPath("$.result[1].admin").value(""))  // 확장 전 행은 빈 문자열
				// tenant 테이블 미보유 필드는 플레이스홀더(Sync 관측 편입 시 복원)
				.andExpect(jsonPath("$.result[0].domain").value(""))
				.andExpect(jsonPath("$.result[0].calls").value(0))
				.andExpect(jsonPath("$.result[0].bars.length()").value(24));
	}

	@Test
	void 생성은_ONBOARDING_으로_저장돼_초기_admin_메모와_함께_맨_앞에_나온다() throws Exception {
		mvc.perform(post("/api/v1/tenants")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\"대신증권\",\"env\":\"PoC\",\"admin\":\"홍길동\","
								+ "\"email\":\"gd.hong@daishin.com\",\"memo\":\"8월 PoC 착수\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));

		mvc.perform(get("/api/v1/tenants"))
				.andExpect(jsonPath("$.result.length()").value(4))
				.andExpect(jsonPath("$.result[0].name").value("대신증권"))
				// 연결 상태는 Sync 채널 기준 — 신규 테넌트는 "미연결"(ONBOARDING)로 시작한다(IA).
				.andExpect(jsonPath("$.result[0].status").value("ONBOARDING"))
				.andExpect(jsonPath("$.result[0].env").value("PoC")) // PoC→POC→PoC 왕복
				// 생성 폼 필드가 보존된다 — ALPHA-121 수용 기준의 핵심.
				.andExpect(jsonPath("$.result[0].admin").value("홍길동"))
				.andExpect(jsonPath("$.result[0].email").value("gd.hong@daishin.com"))
				.andExpect(jsonPath("$.result[0].memo").value("8월 PoC 착수"));
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
	void 구_표기_env_는_전환_기간_동안_수용된다() throws Exception {
		// API·UI 는 독립 배포라 구 UI(Prod/Dev)가 신 API 를 부르는 창이 있다 — 어휘도
		// 확장-수축을 따른다: 전환 기간 수용, 제거는 수축(구 UI 소멸) 시점.
		mvc.perform(post("/api/v1/tenants")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\"구표기증권\",\"env\":\"Prod\",\"admin\":\"홍길동\","
								+ "\"email\":\"gd.hong@daishin.com\"}"))
				.andExpect(status().isOk());
		mvc.perform(get("/api/v1/tenants"))
				.andExpect(jsonPath("$.result[0].env").value("Production")); // Prod→PROD→Production
	}

	@Test
	void 필수_필드_누락과_형식_밖_이메일은_400이다() throws Exception {
		mvc.perform(post("/api/v1/tenants")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\" \",\"env\":\"PoC\",\"admin\":\"홍길동\","
								+ "\"email\":\"gd.hong@daishin.com\"}"))
				.andExpect(status().isBadRequest());
		// 온보딩 연락 창구가 되는 이메일 — '@' 없음·로컬/도메인 결측이 원장에 남으면
		// 기록의 의미가 없다(UI 정규식은 직접 API 호출의 신뢰경계가 아니다).
		for (String email : new String[] {"not-an-email", "a@", "@b.com", " @ "}) {
			mvc.perform(post("/api/v1/tenants")
							.contentType(MediaType.APPLICATION_JSON)
							.content("{\"name\":\"대신증권\",\"env\":\"PoC\",\"admin\":\"홍길동\","
									+ "\"email\":\"" + email + "\"}"))
					.andExpect(status().isBadRequest());
		}
		mvc.perform(post("/api/v1/tenants"))
				.andExpect(status().isBadRequest());
	}

	@Test
	void 과대_길이_메모는_400이다() throws Exception {
		// 목록 응답이 memo 를 전건 실어 나른다 — 무제한 TEXT 저장이 응답을 팽창시킨다.
		mvc.perform(post("/api/v1/tenants")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\"대신증권\",\"env\":\"PoC\",\"admin\":\"홍길동\","
								+ "\"email\":\"gd.hong@daishin.com\",\"memo\":\"" + "가".repeat(2001)
								+ "\"}"))
				.andExpect(status().isBadRequest());
	}
}
