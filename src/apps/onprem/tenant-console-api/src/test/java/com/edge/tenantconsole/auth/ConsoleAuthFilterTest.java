package com.edge.tenantconsole.auth;

import com.edge.tenantconsole.config.TenantContextProperties;
import com.edge.tenantconsole.controller.ConsoleSessionController;
import com.edge.tenantconsole.controller.DashboardController;
import com.edge.tenantconsole.controller.ReviewController;
import com.edge.tenantconsole.controller.ScreeningController;
import com.edge.tenantconsole.entity.PolicyVersionEntity;
import com.edge.tenantconsole.entity.ScreeningRuleEntity;
import com.edge.tenantconsole.repository.PolicyVersionRepository;
import com.edge.tenantconsole.repository.ScreeningRuleRepository;
import com.edge.tenantconsole.service.ScreeningService;
import com.edge.tenantconsole.entity.AnalysisItemEntity;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.model.TrafficSummary;
import com.edge.tenantconsole.repository.MemberRepository;
import com.edge.tenantconsole.repository.PublicationRepository;
import com.edge.tenantconsole.repository.ReviewItemRepository;
import com.edge.tenantconsole.service.ConsoleActionLogService;
import com.edge.tenantconsole.service.ConsoleSessionService;
import com.edge.tenantconsole.service.DashboardService;
import com.edge.tenantconsole.service.ReviewService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Limit;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
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
	private static final SessionMember ADMIN =
			new SessionMember(1L, "admin@demo.edge.local", "관리자", "TENANT_ADMIN");

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
		public int publish(String analysisItemId, String etfTicker, LocalDate tradeDate,
				String publishedSummary) {
			return 1;
		}
	}

	/** 원장 대역 — 이메일→현재 role 맵에 있으면 활성 계정(id 1 고정), 없으면 비활성/삭제(Optional.empty). */
	private static final class StubMembers implements MemberRepository {
		private final Map<String, String> activeRoleByEmail;
		long capturedUpdateNameId = -1;

		StubMembers(Map<String, String> activeRoleByEmail) {
			this.activeRoleByEmail = activeRoleByEmail;
		}

		@Override
		public Optional<MemberEntity> findByEmailAndActiveTrue(String email) {
			String role = activeRoleByEmail.get(email);
			return role == null ? Optional.empty()
					: Optional.of(new MemberEntity(1L, email, "n", role, true, "h"));
		}

		@Override
		public Optional<MemberEntity> findById(Long id) {
			return Optional.empty();
		}

		@Override
		public List<MemberEntity> findAllOrderByMemberId() {
			return List.of();
		}

		@Override
		public List<Long> lockActiveAdminIds() {
			return List.of();
		}

		@Override
		public boolean existsByEmail(String email) {
			return false;
		}

		@Override
		public int deactivate(long id) {
			return 0;
		}

		@Override
		public int updateRole(long id, String role, String expectedRole) {
			return 0;
		}

		@Override
		public int updateName(long id, String name) {
			this.capturedUpdateNameId = id;
			return 1;
		}

		@Override
		public long count() {
			return 0;
		}

		@Override
		public MemberEntity save(MemberEntity member) {
			return member;
		}

		@Override
		public void touchLastLogin(long id) {
		}
	}

	private StubMembers members;
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		// 검수 액션의 기록·감사는 이 테스트의 관심사 밖 — 최소 no-op 대역으로 채운다.
		ReviewService reviewService = new ReviewService(new StubItems(), new StubPublications(),
				task -> task, new StubHistoryRepo(), new StubCheckRepo(), new StubPolicyRules(),
				members, new ConsoleActionLogService(null, null) {
					@Override
					public void record(SessionMember actor, String action, String targetType,
							String targetId, java.util.Map<String, Object> detail, String clientIp) {
					}
				});
		// 원장 현재 상태 — reviewer=CR·ro=RO 는 활성, downgraded 는 세션엔 CR 이나 원장은 RO(강등).
		members = new StubMembers(Map.of(
				"reviewer@demo.edge.local", "COMPLIANCE_REVIEWER",
				"ro@demo.edge.local", "READ_ONLY",
				"downgraded@demo.edge.local", "READ_ONLY",
				"admin@demo.edge.local", "TENANT_ADMIN"));
		mvc = MockMvcBuilders.standaloneSetup(
						new ReviewController(reviewService),
						new ConsoleSessionController(new ConsoleSessionService(members),
								new TenantContextProperties("KB증권", "kbsec.com", "KB")),
						new DashboardController(
								new DashboardService(since -> new TrafficSummary(0, 0))),
						new ScreeningController(new ScreeningService(new StubPolicyVersions(),
								new StubPolicyRules(), members, new ConsoleActionLogService(null, null) {
									@Override
									public void record(SessionMember actor, String action, String targetType,
											String targetId, java.util.Map<String, Object> detail,
											String clientIp) {
									}
								})))
				.addFilters(new ConsoleAuthFilter(members))
				.build();
	}

	private static final class StubHistoryRepo
			implements com.edge.tenantconsole.repository.AnalysisItemStatusHistoryRepository {
		@Override
		public com.edge.tenantconsole.entity.AnalysisItemStatusHistoryEntity save(
				com.edge.tenantconsole.entity.AnalysisItemStatusHistoryEntity history) {
			return history;
		}

		@Override
		public List<com.edge.tenantconsole.entity.AnalysisItemStatusHistoryEntity>
				findByAnalysisItemIdOrderByStatusHistoryIdAsc(String analysisItemId) {
			return List.of();
		}
	}

	private static final class StubCheckRepo
			implements com.edge.tenantconsole.repository.ScreeningCheckRepository {
		@Override
		public List<com.edge.tenantconsole.entity.ScreeningCheckEntity>
				findByAnalysisItemIdOrderByScreeningCheckId(String analysisItemId) {
			return List.of();
		}

		@Override
		public List<com.edge.tenantconsole.entity.ScreeningCheckEntity>
				findByAnalysisItemIdInAndResultOrderByScreeningCheckId(
						java.util.Collection<String> analysisItemIds, String result) {
			return List.of();
		}
	}

	/** 발행 대역 — 인가 판정만 보는 테스트라 IDENTITY 채번만 흉내낸다(리플렉션). */
	private static final class StubPolicyVersions implements PolicyVersionRepository {
		private final List<PolicyVersionEntity> stored = new ArrayList<>();
		private long nextId = 1;

		@Override
		public Optional<PolicyVersionEntity> findActive() {
			return stored.stream()
					.filter(v -> v.getActivatedAt() != null && v.getDeactivatedAt() == null)
					.findFirst();
		}

		@Override
		public int maxVersionNo() {
			return stored.stream().mapToInt(PolicyVersionEntity::getVersionNo).max().orElse(0);
		}

		@Override
		public int deactivate(long id) {
			return 0;
		}

		@Override
		public PolicyVersionEntity save(PolicyVersionEntity version) {
			org.springframework.test.util.ReflectionTestUtils.setField(version, "policyVersionId", nextId++);
			stored.add(version);
			return version;
		}

		@Override
		public List<PolicyVersionEntity> findAllByOrderByVersionNoDesc() {
			return List.copyOf(stored);
		}
	}

	private static final class StubPolicyRules implements ScreeningRuleRepository {
		private final List<ScreeningRuleEntity> stored = new ArrayList<>();
		private long nextId = 1;

		@Override
		public List<ScreeningRuleEntity> findByPolicyVersionIdOrderByScreeningRuleId(long policyVersionId) {
			return stored.stream().filter(r -> r.getPolicyVersionId() == policyVersionId).toList();
		}

		@Override
		public ScreeningRuleEntity save(ScreeningRuleEntity rule) {
			org.springframework.test.util.ReflectionTestUtils.setField(rule, "screeningRuleId", nextId++);
			stored.add(rule);
			return rule;
		}

		@Override
		public List<ScreeningRuleEntity> findByScreeningRuleIdIn(java.util.Collection<Long> ruleIds) {
			return List.of();
		}
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
	void 정책_변경은_Compliance_Reviewer_전용이다() throws Exception {
		// WHY: 정책 변경 = 새 버전 발행(permission-matrix "정책 변경" 행 = CR 전용).
		// screening 도메인의 DB 전환으로 mock 한시 예외(전 역할)가 해제된다.
		mvc.perform(post("/api/v1/screening/words").session(sessionOf(READ_ONLY))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"급등 확실\",\"risk\":\"HIGH\",\"action\":\"BLOCK\"}"))
				.andExpect(status().isForbidden());
		mvc.perform(post("/api/v1/screening/words/1/toggle").session(sessionOf(ADMIN)))
				.andExpect(status().isForbidden());
		mvc.perform(patch("/api/v1/screening/criteria").session(sessionOf(READ_ONLY))
						.contentType(MediaType.APPLICATION_JSON).content("{\"minSources\":1}"))
				.andExpect(status().isForbidden());
		mvc.perform(patch("/api/v1/screening/disclaimer").session(sessionOf(ADMIN))
						.contentType(MediaType.APPLICATION_JSON).content("{\"text\":\"문구\"}"))
				.andExpect(status().isForbidden());

		mvc.perform(post("/api/v1/screening/words").session(sessionOf(REVIEWER))
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"text\":\"급등 확실\",\"risk\":\"HIGH\",\"action\":\"BLOCK\"}"))
				.andExpect(status().isOk());
	}

	@Test
	void 정책_조회는_전_역할이고_버전_이력도_조회다() throws Exception {
		mvc.perform(get("/api/v1/screening/words").session(sessionOf(READ_ONLY)))
				.andExpect(status().isOk());
		mvc.perform(get("/api/v1/screening/versions").session(sessionOf(READ_ONLY)))
				.andExpect(status().isOk());
		mvc.perform(get("/api/v1/screening/versions"))
				.andExpect(status().isUnauthorized());
	}

	@Test
	void 검수_상세는_전_역할_조회다() throws Exception {
		// WHY: 감사 열람(검사 결과·상태 이력)은 별도 Audit 메뉴가 아니라 상세로
		// 확인한다(콘솔 IA) — 조회는 매트릭스대로 전 역할, 미인증은 fail-closed.
		mvc.perform(get("/api/v1/review/items/er-1").session(sessionOf(READ_ONLY)))
				.andExpect(status().isOk());
		mvc.perform(get("/api/v1/review/items/er-1"))
				.andExpect(status().isUnauthorized());
	}

	@Test
	void 대시보드_트래픽은_인증_필수_전_역할_조회다() throws Exception {
		// WHY: Dashboard 는 전 역할 공통 화면이다(permission-matrix) — 단 메트릭도
		// 콘솔 내부 관측 데이터라 미인증엔 fail-closed(401)여야 한다.
		mvc.perform(get("/api/v1/dashboard/traffic"))
				.andExpect(status().isUnauthorized());
		mvc.perform(get("/api/v1/dashboard/traffic").session(sessionOf(READ_ONLY)))
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
	void 차단도_Compliance_Reviewer_전용이다() throws Exception {
		// permission-matrix.md "검수 액션(…차단)" = CR — 신설 라우트(ALPHA-437)도 동일 강제.
		mvc.perform(post("/api/v1/review/items/er-1/block").session(sessionOf(READ_ONLY)))
				.andExpect(status().isForbidden())
				.andExpect(jsonPath("$.code").value("CNSL4030"));
		mvc.perform(post("/api/v1/review/items/er-1/block").session(sessionOf(REVIEWER))
						.contentType(org.springframework.http.MediaType.APPLICATION_JSON)
						.content("{\"reason\":\"사유\"}"))
				.andExpect(status().isOk());
	}

	@Test
	void 수정_승인도_Compliance_Reviewer_전용이다() throws Exception {
		mvc.perform(post("/api/v1/review/items/er-1/approve-edited").session(sessionOf(READ_ONLY)))
				.andExpect(status().isForbidden())
				.andExpect(jsonPath("$.code").value("CNSL4030"));
		mvc.perform(post("/api/v1/review/items/er-1/approve-edited").session(sessionOf(REVIEWER))
						.contentType(org.springframework.http.MediaType.APPLICATION_JSON)
						.content("{\"edited_summary\":\"수정 문구\"}"))
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
	void 역할_변경은_TENANT_ADMIN_전용이다() throws Exception {
		// permission-matrix.md "Users & Roles = TA 전용" — 역할 부여·변경(ALPHA-499)도 동일.
		mvc.perform(patch("/api/v1/members/9/role").session(sessionOf(REVIEWER)))
				.andExpect(status().isForbidden())
				.andExpect(jsonPath("$.code").value("CNSL4030"));
		// 이 셋업엔 MemberController 가 없어 404 = 필터를 통과했다는 증거다(로그인 테스트와 동일 기법).
		mvc.perform(patch("/api/v1/members/9/role").session(sessionOf(ADMIN)))
				.andExpect(status().isNotFound());
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
	void 비활성화된_계정의_기존_세션은_다음_요청에서_401이다() throws Exception {
		// 로그인 후 비활성화된 계정(원장에 활성 레코드 없음) — 세션 role 이 무엇이든 즉시
		// 재로그인 요구다. deactivate 가 세션 만료를 기다리지 않고 반영된다(ALPHA-119).
		SessionMember gone =
				new SessionMember(9L, "gone@demo.edge.local", "탈퇴", "COMPLIANCE_REVIEWER");
		mvc.perform(get("/api/v1/review/items").session(sessionOf(gone)))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.code").value("CNSL4011"));
	}

	@Test
	void 세션의_memberId_가_원장과_다르면_원장_정체성으로_갱신된다() throws Exception {
		// 정체성 SSOT 는 원장이다 — DB 재시드 등으로 같은 이메일이 다른 id 로 재생성되면
		// 세션의 옛 id 로 다른 행을 갱신하는 사고를 막는다. REVIEWER 세션 id=2, 원장 id=1
		// → 프로필 PATCH 는 원장 id(1)의 행을 갱신해야 한다.
		mvc.perform(patch("/api/v1/session/profile").session(sessionOf(REVIEWER))
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"새이름\"}"))
				.andExpect(status().isOk());
		assertThat(members.capturedUpdateNameId).isEqualTo(1L);
	}

	@Test
	void 원장의_이름_변경이_다음_요청_세션_주체에_반영된다() throws Exception {
		// 프로필 이름은 member 원장이 SSOT(ALPHA-500) — 같은 계정의 다른 세션(다른 탭)에서
		// 바뀐 이름도 세션 캐시가 아니라 다음 요청의 원장 재검증으로 반영된다(role 과 동일
		// 메커니즘). StubMembers 원장의 현재 이름("n")이 세션의 옛 이름을 대체해야 한다.
		mvc.perform(get("/api/v1/session").session(sessionOf(REVIEWER)))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.name").value("n"));
	}

	@Test
	void 인가는_세션이_아니라_원장의_현재_role_로_판정한다() throws Exception {
		// 세션엔 CR 로 로그인했지만 원장에서 RO 로 강등된 계정 — 검수 액션은 세션 캐시가
		// 아니라 원장 현재 role(RO)로 판정돼 즉시 거부된다(역할 회수 즉시 반영).
		SessionMember staleCr =
				new SessionMember(2L, "downgraded@demo.edge.local", "강등", "COMPLIANCE_REVIEWER");
		mvc.perform(post("/api/v1/review/items/er-1/approve").session(sessionOf(staleCr)))
				.andExpect(status().isForbidden())
				.andExpect(jsonPath("$.code").value("CNSL4030"));
	}

	@Test
	void 로그인은_유일한_공개_표면이다() throws Exception {
		// 필터를 통과해 MVC 까지 도달한다 — 이 셋업엔 AuthController 가 없어 404 가
		// 곧 "차단되지 않았다"의 증거다(401/403 이면 필터가 막은 것).
		mvc.perform(post("/api/v1/auth/login"))
				.andExpect(status().isNotFound());
	}
}
