package com.edge.tenantconsole.auth;

import com.edge.tenantconsole.controller.ConsoleSessionController;
import com.edge.tenantconsole.controller.ReviewController;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.mock.SessionMockStore;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.ReviewItemRepository;
import com.edge.tenantconsole.service.ConsoleSessionService;
import com.edge.tenantconsole.service.ReviewService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Limit;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 인가 계약(permission-matrix.md)을 검증한다: 미인증 = 전 표면 401(fail-closed),
 * 검수 액션 = Compliance Reviewer 전용, 매핑 없는 표면 = 403(fail-closed).
 * 매트릭스 "API 매핑" 표와 필터 RULES 가 1:1 이라는 전제가 이 테스트의 WHY 다.
 */
class ConsoleAuthFilterTest {

	private static final SessionMember REVIEWER =
			new SessionMember(2L, "reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER");
	private static final SessionMember READ_ONLY =
			new SessionMember(4L, "ro@demo.edge.local", "열람자", "READ_ONLY");

	private static final class StubItems implements ReviewItemRepository {
		@Override
		public List<AnalysisItemEntity> findByStatusOrderByReceivedAtAsc(String status, Limit limit) {
			return List.of();
		}

		@Override
		public Optional<AnalysisItemEntity> findById(String id) {
			return Optional.of(new AnalysisItemEntity(id, "069500", "KODEX 200",
					LocalDate.of(2026, 7, 22), "요약", null, "LOW", "REVIEW_REQUIRED",
					null, null, null));
		}

		@Override
		public int decide(String id, String decidedStatus) {
			return 1;
		}
	}

	private static final class StubPublications implements PublicationRepository {
		@Override
		public int publish(String analysisItemId, String etfTicker, LocalDate tradeDate) {
			return 1;
		}
	}

	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		ReviewService reviewService = new ReviewService(new StubItems(), new StubPublications());
		mvc = MockMvcBuilders.standaloneSetup(
						new ReviewController(reviewService),
						new ConsoleSessionController(new ConsoleSessionService(new SessionMockStore())))
				.addFilters(new ConsoleAuthFilter())
				.build();
	}

	private MockHttpSession sessionOf(SessionMember member) {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionMember.SESSION_KEY, member);
		return session;
	}

	@Test
	void 미인증_요청은_전_표면에서_401이다() throws Exception {
		mvc.perform(get("/api/v1/review/items"))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.isSuccess").value(false))
				.andExpect(jsonPath("$.code").value("CNSL4011"));
		mvc.perform(post("/api/v1/review/items/er-1/approve"))
				.andExpect(status().isUnauthorized());
	}

	@Test
	void 조회는_전_역할_공통이다() throws Exception {
		mvc.perform(get("/api/v1/review/items").session(sessionOf(READ_ONLY)))
				.andExpect(status().isOk());
	}

	@Test
	void 검수_액션은_Compliance_Reviewer_전용이다() throws Exception {
		mvc.perform(post("/api/v1/review/items/er-1/approve").session(sessionOf(READ_ONLY)))
				.andExpect(status().isForbidden())
				.andExpect(jsonPath("$.code").value("CNSL4030"));
		mvc.perform(post("/api/v1/review/items/er-1/approve").session(sessionOf(REVIEWER)))
				.andExpect(status().isOk());
	}

	@Test
	void mock_콘솔_표면은_인증만_요구하고_전_역할을_허용한다() throws Exception {
		// mock 데이터 단계(ALPHA-513)의 한시 결정 — 미인증은 여전히 401(fail-closed)
		// 이고, 인증되면 역할과 무관하게 허용한다. 도메인별 DB 전환 시 이 테스트는
		// permission-matrix.md 역할 세분화 검증으로 교체된다.
		mvc.perform(get("/api/v1/session"))
				.andExpect(status().isUnauthorized());
		mvc.perform(get("/api/v1/session").session(sessionOf(READ_ONLY)))
				.andExpect(status().isOk());
	}

	@Test
	void 매핑_없는_콘솔_표면은_인증돼도_403이다() throws Exception {
		// permission-matrix.md 에 행이 없는 표면은 거부가 기본(fail-closed) —
		// 새 엔드포인트가 매핑 없이 배포되는 것을 구조적으로 막는다.
		mvc.perform(get("/api/v1/unknown").session(sessionOf(REVIEWER)))
				.andExpect(status().isForbidden());
	}

	@Test
	void matrix_parameter_우회는_인증을_건너뛰지_못한다() throws Exception {
		// `/api;x=y/...` 는 MVC 가 매트릭스 파라미터를 벗겨 검수 API 로 매핑한다 —
		// 필터도 같은 정규화를 적용해 미인증 요청을 401 로 막아야 한다(fail-closed).
		mvc.perform(post("/api;x=y/v1/review/items/er-1/approve"))
				.andExpect(status().isUnauthorized());
	}

	@Test
	void 인코딩된_matrix_구분자_우회도_차단된다() throws Exception {
		// `%3B`(인코딩된 ;)는 MVC 가 디코딩 후 매핑하므로, 필터도 디코딩 후 판정해야
		// `/api%3Bx=y/...` 우회를 막는다. requestURI 를 직접 지정해 인코딩을 보존한다.
		mvc.perform(post("/x").requestAttr("bypass", "n")
						.with(request -> {
							request.setRequestURI("/api%3Bx=y/v1/review/items/er-1/approve");
							request.setServletPath("/api%3Bx=y/v1/review/items/er-1/approve");
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
}
