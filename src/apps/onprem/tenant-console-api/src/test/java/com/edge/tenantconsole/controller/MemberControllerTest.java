package com.edge.tenantconsole.controller;

import com.edge.common.exception.ExceptionAdvice;
import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.ChangeMemberRoleRequest;
import com.edge.tenantconsole.dto.CreateMemberRequest;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.Member;
import com.edge.tenantconsole.service.MemberService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 사용자 관리 표면(ALPHA-119) HTTP 계약 검증 — WHY: (1) 목록·등록 응답은 UI 계약대로
 * camelCase(is_active → status·last_login_at → lastLogin)로 나가고, (2) 등록·비활성화의
 * 감사 주체(actor)는 요청 파라미터가 아니라 "세션"에서 취해 서비스로 전달돼야 하며
 * (감사 주체 위조 방지), (3) 도메인 예외가 상태코드로 매핑된다(중복=409).
 */
class MemberControllerTest {

	private FakeService service;
	private MockMvc mvc;
	private final SessionMember admin =
			new SessionMember(7, "admin@demo.edge.local", "관리자", "TENANT_ADMIN");

	@BeforeEach
	void setUp() {
		service = new FakeService();
		mvc = MockMvcBuilders.standaloneSetup(new MemberController(service))
				.setControllerAdvice(new ExceptionAdvice())
				.build();
	}

	@Test
	void 목록은_원장을_camelCase_로_반환한다() throws Exception {
		service.listResult = List.of(
				new Member(1, "a@kbsec.com", "김철수", "TENANT_ADMIN", true, "h", null),
				new Member(2, "b@kbsec.com", "박영희", "OPERATOR", false, null, null));
		mvc.perform(get("/api/v1/members"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.code").value("COMMON200"))
				.andExpect(jsonPath("$.result[0].id").value(1))
				.andExpect(jsonPath("$.result[0].email").value("a@kbsec.com"))
				.andExpect(jsonPath("$.result[0].status").value("ACTIVE"))
				.andExpect(jsonPath("$.result[1].status").value("INACTIVE"));
	}

	@Test
	void 등록은_세션의_actor_로_서비스를_호출하고_생성결과를_반환한다() throws Exception {
		service.registerResult = new Member(9, "new@kbsec.com", "신규", "OPERATOR", true, null, null);
		mvc.perform(post("/api/v1/members")
						.sessionAttr(SessionMember.SESSION_KEY, admin)
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"new@kbsec.com\",\"name\":\"신규\",\"role\":\"OPERATOR\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.result.id").value(9))
				.andExpect(jsonPath("$.result.status").value("ACTIVE"));
		// 감사 주체는 요청 본문이 아니라 세션에서 온다.
		assertThat(service.capturedActor.memberId()).isEqualTo(7);
	}

	@Test
	void 비활성화는_경로_id_와_세션_actor_로_서비스를_호출한다() throws Exception {
		mvc.perform(post("/api/v1/members/9/deactivate")
						.sessionAttr(SessionMember.SESSION_KEY, admin))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		assertThat(service.capturedDeactivateId).isEqualTo(9L);
		assertThat(service.capturedActor.memberId()).isEqualTo(7);
	}

	@Test
	void 역할_변경은_경로_id_와_세션_actor_로_서비스를_호출한다() throws Exception {
		mvc.perform(patch("/api/v1/members/9/role")
						.sessionAttr(SessionMember.SESSION_KEY, admin)
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"role\":\"OPERATOR\"}"))
				.andExpect(status().isOk())
				.andExpect(jsonPath("$.isSuccess").value(true));
		assertThat(service.capturedChangeRoleId).isEqualTo(9L);
		assertThat(service.capturedChangeRoleRequest.role()).isEqualTo("OPERATOR");
		assertThat(service.capturedActor.memberId()).isEqualTo(7);
	}

	@Test
	void 자기_자신_역할_변경은_403_으로_매핑된다() throws Exception {
		service.changeRoleThrow = new GeneralException(ConsoleErrorStatus.SELF_ROLE_CHANGE);
		mvc.perform(patch("/api/v1/members/7/role")
						.sessionAttr(SessionMember.SESSION_KEY, admin)
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"role\":\"COMPLIANCE_REVIEWER\"}"))
				.andExpect(status().isForbidden())
				.andExpect(jsonPath("$.code").value("CNSL4031"));
	}

	@Test
	void 중복_이메일_등록은_409_로_매핑된다() throws Exception {
		service.registerThrow = new GeneralException(ConsoleErrorStatus.DUPLICATE_MEMBER_EMAIL);
		mvc.perform(post("/api/v1/members")
						.sessionAttr(SessionMember.SESSION_KEY, admin)
						.contentType(MediaType.APPLICATION_JSON)
						.content("{\"email\":\"dup@kbsec.com\",\"name\":\"중복\",\"role\":\"OPERATOR\"}"))
				.andExpect(status().isConflict())
				.andExpect(jsonPath("$.code").value("CNSL4093"));
	}

	/** MemberService 를 손수 대역화 — 컨트롤러의 HTTP·세션 관심사만 검증한다. */
	private static final class FakeService extends MemberService {
		List<Member> listResult = List.of();
		Member registerResult;
		RuntimeException registerThrow;
		RuntimeException changeRoleThrow;
		SessionMember capturedActor;
		long capturedDeactivateId = -1;
		long capturedChangeRoleId = -1;
		ChangeMemberRoleRequest capturedChangeRoleRequest;

		FakeService() {
			super(null, null);
		}

		@Override
		public void changeRole(long memberId, ChangeMemberRoleRequest request,
				SessionMember actor, String clientIp) {
			this.capturedChangeRoleId = memberId;
			this.capturedChangeRoleRequest = request;
			this.capturedActor = actor;
			if (changeRoleThrow != null) {
				throw changeRoleThrow;
			}
		}

		@Override
		public List<Member> list() {
			return listResult;
		}

		@Override
		public Member register(CreateMemberRequest request, SessionMember actor, String clientIp) {
			this.capturedActor = actor;
			if (registerThrow != null) {
				throw registerThrow;
			}
			return registerResult;
		}

		@Override
		public void deactivate(long memberId, SessionMember actor, String clientIp) {
			this.capturedDeactivateId = memberId;
			this.capturedActor = actor;
		}
	}
}
