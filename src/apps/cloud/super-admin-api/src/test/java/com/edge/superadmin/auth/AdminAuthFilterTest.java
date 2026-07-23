package com.edge.superadmin.auth;

import com.edge.superadmin.controller.TenantController;
import com.edge.superadmin.mock.TenantMockStore;
import com.edge.superadmin.service.TenantService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.config.BeanDefinition;
import org.springframework.context.annotation.ClassPathScanningCandidateComponentProvider;
import org.springframework.core.type.filter.AnnotationTypeFilter;
import org.springframework.http.HttpMethod;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.request;
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
				// 표면 전수 테스트가 빈 본문 POST 로 컨트롤러까지 들어간다 — 검증 400 이
				// advice 없이 ServletException 으로 새지 않게 실제 배선과 같게 둔다.
				.setControllerAdvice(new com.edge.common.exception.ExceptionAdvice())
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

	/**
	 * RULES 와 실제 컨트롤러 표면의 1:1 을 검증한다 — 표면을 손으로 복제하지 않고
	 * 앱 패키지의 @RestController 를 classpath 스캔해 매핑 애노테이션(전 경로)에서
	 * 열거하므로, 새 컨트롤러·새 엔드포인트를 RULES 등록 없이 추가하면 이 테스트가
	 * 깨진다(등록 누락 = 배포돼도 403 으로 닫히는 회귀를 여기서 잡는다).
	 */
	@Test
	void 전_컨트롤러_표면이_RULES_에_등록돼_있다() throws Exception {
		int surfaceCount = 0;
		for (Class<?> controller : restControllers()) {
			for (Method handler : controller.getDeclaredMethods()) {
				for (Map.Entry<String, List<String>> mapping : mappingsOf(handler).entrySet()) {
					String httpMethod = mapping.getKey();
					for (String declaredPath : mapping.getValue()) {
						surfaceCount++;
						// 경로 변수는 임의 세그먼트로 치환 — RULES 의 [^/]+ 와 대응.
						String path = declaredPath.replaceAll("\\{[^}]+}", "x1");
						boolean isPublicLogin = "POST".equals(httpMethod)
								&& "/api/v1/auth/login".equals(path);

						// 인증된 요청이 401/403 없이 필터를 통과하면 등록된 것이다 — 이
						// 셋업엔 TenantController 만 있어 그 밖 표면은 404 가 통과의 증거다.
						int status = mvc.perform(request(HttpMethod.valueOf(httpMethod), path)
										.session(sessionOf(OPERATOR)))
								.andReturn().getResponse().getStatus();
						assertThat(status)
								.as("%s %s 는 RULES 에 등록돼 필터를 통과해야 한다", httpMethod, path)
								.isNotIn(401, 403);

						// 미인증은 login 을 제외한 전 표면 401 — fail-closed 의 반대편 절반.
						if (!isPublicLogin) {
							mvc.perform(request(HttpMethod.valueOf(httpMethod), path))
									.andExpect(status().isUnauthorized());
						}
					}
				}
			}
		}
		// 열거 자체가 얇으면(스캔 오류) 테스트가 헛돌았다는 뜻이다 — 현 표면 12종.
		assertThat(surfaceCount).isGreaterThanOrEqualTo(12);
	}

	/** 앱 패키지의 @RestController 전수 — 수동 목록이 아니라 classpath 스캔. */
	private List<Class<?>> restControllers() throws ClassNotFoundException {
		ClassPathScanningCandidateComponentProvider scanner =
				new ClassPathScanningCandidateComponentProvider(false);
		scanner.addIncludeFilter(new AnnotationTypeFilter(RestController.class));
		List<Class<?>> controllers = new ArrayList<>();
		for (BeanDefinition definition : scanner.findCandidateComponents("com.edge.superadmin")) {
			controllers.add(Class.forName(definition.getBeanClassName()));
		}
		return controllers;
	}

	/** 핸들러 메서드의 (HTTP 메서드 → 선언 경로 전부) — 이 코드베이스가 쓰는 3종만 스캔. */
	private Map<String, List<String>> mappingsOf(Method handler) {
		Map<String, List<String>> mappings = new LinkedHashMap<>();
		GetMapping get = handler.getAnnotation(GetMapping.class);
		if (get != null) {
			mappings.put("GET", List.of(get.value()));
		}
		PostMapping post = handler.getAnnotation(PostMapping.class);
		if (post != null) {
			mappings.put("POST", List.of(post.value()));
		}
		PatchMapping patch = handler.getAnnotation(PatchMapping.class);
		if (patch != null) {
			mappings.put("PATCH", List.of(patch.value()));
		}
		return mappings;
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
