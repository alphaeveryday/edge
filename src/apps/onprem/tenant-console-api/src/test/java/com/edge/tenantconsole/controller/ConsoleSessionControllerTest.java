package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.config.TenantContextProperties;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.repository.MemberRepository;
import com.edge.tenantconsole.service.ConsoleSessionService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * UI 계약(tenant-console-ui session 도메인) 검증 — WHY(ALPHA-500): (1) 세션 표면의
 * name 은 mock 싱글턴이 아니라 인증 주체(SessionMember = member 원장)의 실제 이름이어야
 * 하고, (2) 테넌트 컨텍스트는 배포 설정(console.tenant.*)이 소스이며, (3) 표시 이름
 * 변경은 mock 이 아니라 member 원장에 기록되고 같은 세션의 다음 조회에 즉시 반영돼야
 * 한다(다른 세션은 필터의 원장 재검증이 반영 — ConsoleAuthFilterTest).
 */
class ConsoleSessionControllerTest {

	private static final SessionMember REVIEWER =
			new SessionMember(2L, "reviewer@demo.edge.local", "데모 검수자", "COMPLIANCE_REVIEWER");

	private FakeMembers members;
	private MockMvc mvc;

	@BeforeEach
	void setUp() {
		members = new FakeMembers();
		mvc = MockMvcBuilders
				.standaloneSetup(new ConsoleSessionController(
						new ConsoleSessionService(members),
						new TenantContextProperties("KB증권", "kbsec.com", "KB")))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 세션은_인증_주체의_이름과_설정_테넌트_컨텍스트를_반환한다() throws Exception {
		mvc.perform(get("/api/v1/session").sessionAttr(SessionMember.SESSION_KEY, REVIEWER))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true))
				.andExpect(jsonPath("$.code").value("COMMON200"))
				// name·email·role 은 mock 자리표시가 아니라 인증 주체 본인이어야 한다.
				.andExpect(jsonPath("$.result.name").value("데모 검수자"))
				.andExpect(jsonPath("$.result.email").value("reviewer@demo.edge.local"))
				.andExpect(jsonPath("$.result.role").value("COMPLIANCE_REVIEWER"))
				.andExpect(jsonPath("$.result.tenantName").value("KB증권"))
				.andExpect(jsonPath("$.result.tenantDomain").value("kbsec.com"))
				.andExpect(jsonPath("$.result.tenantMark").value("KB"));
	}

	@Test
	void 표시_이름_변경은_원장에_기록되고_같은_세션_조회에_즉시_반영된다() throws Exception {
		MockHttpSession session = new MockHttpSession();
		session.setAttribute(SessionMember.SESSION_KEY, REVIEWER);
		mvc.perform(patch("/api/v1/session/profile").session(session)
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\" 김영서 \"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		// 세션 주체 본인의 원장 행에 trim 된 이름이 기록된다.
		assertThat(members.capturedNameTargetId).isEqualTo(2L);
		assertThat(members.capturedName).isEqualTo("김영서");
		// 같은 세션의 다음 조회는 필터 재검증을 기다리지 않고 즉시 새 이름을 반환한다.
		mvc.perform(get("/api/v1/session").session(session))
				.andExpect(jsonPath("$.result.name").value("김영서"));
	}

	@Test
	void 빈_표시_이름은_400_이고_원장에_닿지_않는다() throws Exception {
		mvc.perform(patch("/api/v1/session/profile")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\" \"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4003"));
		assertThat(members.capturedName).isNull();
	}

	@Test
	void 과대_길이_표시_이름은_400_이고_원장에_닿지_않는다() throws Exception {
		// 프로필은 전 역할 셀프서비스 표면 — blank 만 거르면 무제한 TEXT 가 원장에 영구
		// 저장된다. 상한(100자) 초과는 400 으로 드러낸다(fail-loud).
		mvc.perform(patch("/api/v1/session/profile")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"name\":\"" + "가".repeat(101) + "\"}"))
				.andExpect(status().isBadRequest())
				.andExpect(jsonPath("$.code").value("CNSL4003"));
		assertThat(members.capturedName).isNull();
	}

	@Test
	void 원장_갱신_영향_행이_0_이면_404_다() throws Exception {
		// 세션 주체는 통상 존재하지만, 기록 없는 성공을 만들지 않는 백스톱(fail-loud).
		members.updateNameRows = 0;
		mvc.perform(patch("/api/v1/session/profile")
						.sessionAttr(SessionMember.SESSION_KEY, REVIEWER)
						.contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"김영서\"}"))
				.andExpect(status().isNotFound())
				.andExpect(jsonPath("$.code").value("CNSL4044"));
	}

	/** member 원장 대역 — updateName 호출 캡처만 관심사다. */
	private static final class FakeMembers implements MemberRepository {
		int updateNameRows = 1;
		long capturedNameTargetId = -1;
		String capturedName;

		@Override
		public int updateName(long id, String name) {
			this.capturedNameTargetId = id;
			this.capturedName = name;
			return updateNameRows;
		}

		@Override
		public Optional<MemberEntity> findByEmailAndActiveTrue(String email) {
			return Optional.empty();
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
		public long count() {
			return 0;
		}

		@Override
		public MemberEntity save(MemberEntity member) {
			return member;
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
		public void touchLastLogin(long id) {
		}
	}
}
