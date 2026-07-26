package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.auth.BootstrapAccounts;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.repository.MemberRepository;
import com.edge.tenantconsole.service.AuthService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 인증 계약(ADR-0025 데모 경로)을 검증한다: 로그인 성공 = 역할 실린 세션,
 * 실패 사유는 구분 없는 401(계정 존재 여부 비노출), 로그아웃 = 세션 무효화.
 * Boot 4 는 @WebMvcTest 슬라이스가 없어 standaloneSetup 을 쓴다. 리포지토리(JPA)는
 * 좁은 인터페이스라 페이크로 스텁한다.
 */
class AuthControllerTest {

	private static final BCryptPasswordEncoder ENCODER = new BCryptPasswordEncoder();
	private static final String PASSWORD = "demo-pw-1";
	private static final MemberEntity REVIEWER = new MemberEntity(
			2L, "reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER", true,
			ENCODER.encode(PASSWORD));

	private static final class StubMembers implements MemberRepository {
		MemberEntity member = REVIEWER;
		Long lastLoginTouched = null;

		@Override
		public Optional<MemberEntity> findByEmailAndActiveTrue(String email) {
			return Optional.ofNullable(
					member != null && member.getEmail().equals(email) ? member : null);
		}

		@Override
		public long count() {
			// 시드 불필요 상태 — 로그인 경로의 지연 부트스트랩이 시드를 건너뛰게 한다.
			return 1;
		}

		@Override
		public MemberEntity save(MemberEntity m) {
			return m;
		}

		@Override
		public Optional<MemberEntity> findById(Long id) {
			return Optional.ofNullable(member);
		}

		@Override
		public List<MemberEntity> findAllOrderByMemberId() {
			return member == null ? List.of() : List.of(member);
		}

		@Override
		public List<Long> lockActiveAdminIds() {
			return List.of();
		}

		@Override
		public boolean existsByEmail(String email) {
			return member != null && member.getEmail().equals(email);
		}

		@Override
		public int deactivate(long memberId) {
			return 0;
		}

		@Override
		public int updateRole(long memberId, String role, String expectedRole) {
			return 0;
		}

		@Override
		public void touchLastLogin(long memberId) {
			lastLoginTouched = memberId;
		}
	}

	private StubMembers members;
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		members = new StubMembers();
		AuthService authService = new AuthService(members, new BootstrapAccounts(List.of()),
				org.springframework.transaction.support.TransactionOperations.withoutTransaction());
		mvc = MockMvcBuilders.standaloneSetup(new AuthController(authService))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 로그인_성공은_역할이_실린_세션을_만든다() throws Exception {
		MvcResult result = mvc.perform(post("/api/v1/auth/login")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"Reviewer@demo.edge.local\",\"password\":\"" + PASSWORD + "\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.role").value("COMPLIANCE_REVIEWER"))
				.andExpect(jsonPath("$.result.member_id").value(2))
				.andReturn();

		// 이메일 대소문자는 정규화되고, 세션에 SessionMember 가 실리며, 최근 로그인이 갱신된다.
		SessionMember session = (SessionMember) result.getRequest().getSession(false)
				.getAttribute(SessionMember.SESSION_KEY);
		assertThat(session.role()).isEqualTo("COMPLIANCE_REVIEWER");
		assertThat(members.lastLoginTouched).isEqualTo(2L);
	}

	@Test
	void 비밀번호_불일치와_미존재_계정은_같은_401_코드다() throws Exception {
		mvc.perform(post("/api/v1/auth/login")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"reviewer@demo.edge.local\",\"password\":\"wrong\"}"))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.code").value("CNSL4010"));

		mvc.perform(post("/api/v1/auth/login")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"nobody@demo.edge.local\",\"password\":\"" + PASSWORD + "\"}"))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.code").value("CNSL4010"));
	}

	@Test
	void SSO_전용_계정은_로컬_로그인이_거부된다() throws Exception {
		members.member = new MemberEntity(
				3L, "sso@demo.edge.local", "SSO 사용자", "OPERATOR", true, null);
		mvc.perform(post("/api/v1/auth/login")
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"sso@demo.edge.local\",\"password\":\"any\"}"))
				.andExpect(status().isUnauthorized())
				.andExpect(jsonPath("$.code").value("CNSL4010"));
	}

	@Test
	void 로그아웃은_세션을_무효화한다() throws Exception {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionMember.SESSION_KEY,
				new SessionMember(2L, "reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER"));

		mvc.perform(post("/api/v1/auth/logout").session(session))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		assertThat(session.isInvalid()).isTrue();
	}

	@Test
	void 세션_조회는_현재_주체를_반환한다() throws Exception {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionMember.SESSION_KEY,
				new SessionMember(2L, "reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER"));

		mvc.perform(get("/api/v1/auth/session").session(session))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result.email").value("reviewer@demo.edge.local"))
				.andExpect(jsonPath("$.result.role").value("COMPLIANCE_REVIEWER"));
	}
}
