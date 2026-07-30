package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.ChangeMemberRoleRequest;
import com.edge.tenantconsole.dto.CreateMemberRequest;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.repository.MemberRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 사용자 관리 서비스(ALPHA-119) 규칙 검증 — WHY: (1) 잘못된 입력(어휘 밖 role·빈
 * 이메일·중복)은 원장에 닿기 전에 막고, (2) 성공한 변경은 반드시 감사(actor·action·
 * 대상)로 남으며, (3) 비밀번호는 있을 때만 BCrypt 해시(평문 저장 금지)하고 없으면 SSO
 * 전용(NULL)으로 둔다. 리포지토리·감사는 좁은 계약이라 손수 대역화한다(실 DB 는 IT).
 */
class MemberServiceTest {

	private FakeMembers members;
	private RecordingActionLog actionLog;
	private MemberService service;
	private final SessionMember actor =
			new SessionMember(7, "admin@demo.edge.local", "관리자", "TENANT_ADMIN");

	@BeforeEach
	void setUp() {
		members = new FakeMembers();
		actionLog = new RecordingActionLog();
		service = new MemberService(members, actionLog);
	}

	@Test
	void 등록은_이메일을_정규화하고_비밀번호를_BCrypt_로_저장하며_감사한다() {
		service.register(new CreateMemberRequest("New@KBSEC.com", " 신규 ", "OPERATOR", "pw12345"),
				actor, "10.0.0.1");

		MemberEntity saved = members.saved.get(0);
		assertThat(saved.getEmail()).isEqualTo("new@kbsec.com");   // 소문자·trim 정규화
		assertThat(saved.getName()).isEqualTo("신규");             // trim
		assertThat(saved.getPasswordHash()).isNotNull().isNotEqualTo("pw12345");  // 평문 저장 금지
		assertThat(new BCryptPasswordEncoder().matches("pw12345", saved.getPasswordHash())).isTrue();

		assertThat(actionLog.entries).singleElement().satisfies(e -> {
			assertThat(e.action()).isEqualTo("MEMBER_REGISTERED");
			assertThat(e.actor()).isEqualTo(actor);              // 세션 주체가 감사 주체
			assertThat(e.targetId()).isEqualTo("100");           // 생성된 member_id
			assertThat(e.detail()).containsEntry("email", "new@kbsec.com").containsEntry("role", "OPERATOR");
			assertThat(e.clientIp()).isEqualTo("10.0.0.1");
		});
	}

	@Test
	void 비밀번호_없으면_SSO_전용_NULL_해시로_등록된다() {
		service.register(new CreateMemberRequest("sso@kbsec.com", "SSO", "READ_ONLY", null), actor, "ip");
		assertThat(members.saved.get(0).getPasswordHash()).isNull();
	}

	@Test
	void 어휘_밖_role_은_원장에_닿기_전_400_이고_감사도_남지_않는다() {
		assertThatThrownBy(() -> service.register(
				new CreateMemberRequest("x@kbsec.com", "X", "Owner", "pw12345"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
		assertThat(members.saved).isEmpty();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void role_누락은_NPE_없이_400_이다() {
		// Set.of 의 contains(null) 은 NPE — role 누락이 500 으로 새지 않고 400 으로 걸러져야 한다.
		assertThatThrownBy(() -> service.register(
				new CreateMemberRequest("x@kbsec.com", "X", null, "pw12345"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
		assertThat(members.saved).isEmpty();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void BCrypt_72바이트_초과_비밀번호는_encode_전_400_이다() {
		// 73바이트 ASCII — encode 의 IllegalArgumentException(500)이 아니라 400 으로 걸러야 한다.
		String tooLong = "a".repeat(73);
		assertThatThrownBy(() -> service.register(
				new CreateMemberRequest("x@kbsec.com", "X", "OPERATOR", tooLong), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
		assertThat(members.saved).isEmpty();
	}

	@Test
	void 빈_이메일_등록은_400() {
		assertThatThrownBy(() -> service.register(
				new CreateMemberRequest("  ", "X", "OPERATOR", "pw12345"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
		assertThat(members.saved).isEmpty();
	}

	@Test
	void 중복_이메일은_저장_전_409_이고_감사도_남지_않는다() {
		members.existing = true;
		assertThatThrownBy(() -> service.register(
				new CreateMemberRequest("dup@kbsec.com", "중복", "OPERATOR", "pw12345"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.DUPLICATE_MEMBER_EMAIL));
		assertThat(members.saved).isEmpty();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 비활성화는_대상을_토글하고_감사한다() {
		members.target = new MemberEntity(42L, "op@kbsec.com", "운영자", "OPERATOR", true, null);
		members.deactivateRows = 1;
		service.deactivate(42, actor, "10.0.0.9");
		assertThat(actionLog.entries).singleElement().satisfies(e -> {
			assertThat(e.action()).isEqualTo("MEMBER_DEACTIVATED");
			assertThat(e.targetId()).isEqualTo("42");
		});
	}

	@Test
	void 없는_대상_비활성화는_404_이고_감사도_남지_않는다() {
		members.target = null;  // findById 미존재
		assertThatThrownBy(() -> service.deactivate(999, actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.MEMBER_NOT_FOUND));
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 마지막_활성_관리자_비활성화는_409_이고_감사도_남지_않는다() {
		// 재활성 API·부트스트랩 복구가 없어, 마지막 관리자를 끄면 테넌트가 잠긴다 — 막는다.
		members.target = new MemberEntity(1L, "admin@kbsec.com", "관리자", "TENANT_ADMIN", true, null);
		members.activeAdminIds = new ArrayList<>(List.of(1L));  // 잠긴 활성 관리자 = target 뿐
		assertThatThrownBy(() -> service.deactivate(1, actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.LAST_ADMIN));
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 다른_활성_관리자가_있으면_관리자_비활성화가_허용된다() {
		members.target = new MemberEntity(1L, "admin@kbsec.com", "관리자", "TENANT_ADMIN", true, null);
		members.activeAdminIds = new ArrayList<>(List.of(1L, 2L));  // 다른 활성 관리자 존재
		members.deactivateRows = 1;
		service.deactivate(1, actor, "ip");
		assertThat(actionLog.entries).singleElement()
				.satisfies(e -> assertThat(e.action()).isEqualTo("MEMBER_DEACTIVATED"));
	}

	@Test
	void 이미_비활성인_관리자_재비활성화는_LAST_ADMIN_이_아니라_멱등이다() {
		// 락 이전의 stale active 로 오판하지 않는다 — target 이 잠긴 활성 집합에 없으면(이미
		// 비활성) 다른 관리자가 유일해도 LAST_ADMIN 이 아니라 멱등 처리된다(P2 경쟁 수정).
		members.target = new MemberEntity(1L, "admin@kbsec.com", "관리자", "TENANT_ADMIN", false, null);
		members.activeAdminIds = new ArrayList<>(List.of(2L));  // 활성은 다른 관리자 B 뿐
		members.deactivateRows = 1;
		service.deactivate(1, actor, "ip");
		assertThat(actionLog.entries).singleElement()
				.satisfies(e -> assertThat(e.action()).isEqualTo("MEMBER_DEACTIVATED"));
	}

	@Test
	void 역할_변경은_원장을_갱신하고_이전과_새_역할을_감사한다() {
		members.target = new MemberEntity(42L, "op@kbsec.com", "운영자", "OPERATOR", true, null);
		service.changeRole(42, new ChangeMemberRoleRequest("COMPLIANCE_REVIEWER"), actor, "10.0.0.9");
		assertThat(members.capturedRoleTargetId).isEqualTo(42L);
		assertThat(members.capturedRole).isEqualTo("COMPLIANCE_REVIEWER");
		// 조건부 갱신 — 읽어둔 이전 역할이 UPDATE 조건에 실려야 경쟁 변경 시 감사가 틀리지 않는다.
		assertThat(members.capturedExpectedRole).isEqualTo("OPERATOR");
		assertThat(actionLog.entries).singleElement().satisfies(e -> {
			assertThat(e.action()).isEqualTo("MEMBER_ROLE_CHANGED");
			assertThat(e.actor()).isEqualTo(actor);              // 세션 주체가 감사 주체
			assertThat(e.targetId()).isEqualTo("42");
			// 이전→새 역할이 함께 남아야 "무엇이 회수됐는지"를 감사만으로 재구성할 수 있다.
			assertThat(e.detail()).containsEntry("email", "op@kbsec.com")
					.containsEntry("previousRole", "OPERATOR")
					.containsEntry("newRole", "COMPLIANCE_REVIEWER");
			assertThat(e.clientIp()).isEqualTo("10.0.0.9");
		});
	}

	@Test
	void 어휘_밖_역할로의_변경은_원장에_닿기_전_400_이고_감사도_남지_않는다() {
		members.target = new MemberEntity(42L, "op@kbsec.com", "운영자", "OPERATOR", true, null);
		assertThatThrownBy(() -> service.changeRole(
				42, new ChangeMemberRoleRequest("Owner"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
		assertThat(members.capturedRole).isNull();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 역할_변경의_role_누락은_NPE_없이_400_이다() {
		// register 와 동일 — Set.of 의 contains(null) NPE 로 400 대신 500 이 새는 우회 차단.
		members.target = new MemberEntity(42L, "op@kbsec.com", "운영자", "OPERATOR", true, null);
		assertThatThrownBy(() -> service.changeRole(
				42, new ChangeMemberRoleRequest(null), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
		assertThat(members.capturedRole).isNull();
	}

	@Test
	void 없는_대상_역할_변경은_404_이고_감사도_남지_않는다() {
		members.target = null;  // findById 미존재
		assertThatThrownBy(() -> service.changeRole(
				999, new ChangeMemberRoleRequest("OPERATOR"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.MEMBER_NOT_FOUND));
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 자기_자신의_역할_변경은_403_이고_원장과_감사에_닿지_않는다() {
		// 직무 분리(permission-matrix.md) — TA 가 스스로 CR 을 부여해 검수·정책 권한을
		// 얻는 우회를 막는다. actor(7)와 대상이 같으면 어떤 역할로든 변경 금지.
		members.target = new MemberEntity(7L, "admin@demo.edge.local", "관리자", "TENANT_ADMIN", true, null);
		assertThatThrownBy(() -> service.changeRole(
				7, new ChangeMemberRoleRequest("COMPLIANCE_REVIEWER"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.SELF_ROLE_CHANGE));
		assertThat(members.capturedRole).isNull();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 마지막_활성_관리자의_강등은_409_이고_감사도_남지_않는다() {
		// 비활성화와 같은 이유 — 마지막 관리자의 role 을 바꾸면 사용자 관리 권한이 사라져
		// 테넌트가 잠긴다. 같은 락(lockActiveAdminIds) 재사용으로 동시 강등 경쟁도 직렬화.
		members.target = new MemberEntity(1L, "admin@kbsec.com", "관리자", "TENANT_ADMIN", true, null);
		members.activeAdminIds = new ArrayList<>(List.of(1L));
		assertThatThrownBy(() -> service.changeRole(
				1, new ChangeMemberRoleRequest("OPERATOR"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.LAST_ADMIN));
		assertThat(members.capturedRole).isNull();
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 다른_활성_관리자가_있으면_관리자_강등이_허용된다() {
		members.target = new MemberEntity(1L, "admin@kbsec.com", "관리자", "TENANT_ADMIN", true, null);
		members.activeAdminIds = new ArrayList<>(List.of(1L, 2L));
		service.changeRole(1, new ChangeMemberRoleRequest("OPERATOR"), actor, "ip");
		assertThat(actionLog.entries).singleElement()
				.satisfies(e -> assertThat(e.action()).isEqualTo("MEMBER_ROLE_CHANGED"));
	}

	@Test
	void 비활성_관리자의_강등은_LAST_ADMIN_이_아니다() {
		// 잠긴 활성 집합 멤버십으로 판정(비활성화와 동일) — 이미 비활성인 관리자는 강등해도
		// 활성 관리자 수가 변하지 않으므로 막을 이유가 없다.
		members.target = new MemberEntity(1L, "admin@kbsec.com", "관리자", "TENANT_ADMIN", false, null);
		members.activeAdminIds = new ArrayList<>(List.of(2L));
		service.changeRole(1, new ChangeMemberRoleRequest("OPERATOR"), actor, "ip");
		assertThat(actionLog.entries).singleElement()
				.satisfies(e -> assertThat(e.action()).isEqualTo("MEMBER_ROLE_CHANGED"));
	}

	@Test
	void 같은_역할로의_변경은_원장으로_원자_검증되고_감사가_남지_않는다() {
		// 변경이 없으면 감사 대상은 아니지만, no-op 판정도 stale 읽기가 아니라 조건부
		// UPDATE 로 원장 현재값과 원자적으로 대조돼야 경쟁 변경을 성공으로 오인하지 않는다.
		members.target = new MemberEntity(42L, "op@kbsec.com", "운영자", "OPERATOR", true, null);
		service.changeRole(42, new ChangeMemberRoleRequest("OPERATOR"), actor, "ip");
		assertThat(members.capturedRole).isEqualTo("OPERATOR");
		assertThat(members.capturedExpectedRole).isEqualTo("OPERATOR");
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 같은_역할_변경도_경쟁이_감지되면_409_다() {
		// 읽기와 검증 사이에 다른 트랜잭션이 역할을 바꿨으면 "이미 그 역할"이라는 성공
		// 보고가 거짓이 된다 — no-op 경로도 조건부 갱신 0행이면 409 로 충돌을 드러낸다.
		members.target = new MemberEntity(42L, "op@kbsec.com", "운영자", "OPERATOR", true, null);
		members.updateRoleRows = 0;
		assertThatThrownBy(() -> service.changeRole(
				42, new ChangeMemberRoleRequest("OPERATOR"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.ROLE_CONFLICT));
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 관리자에게_같은_역할_재지정은_LAST_ADMIN_이_아니다() {
		// 강등이 아니면(같은 역할 재지정) 활성 관리자 수가 변하지 않는다 — 마지막 관리자
		// 잠금은 실제 강등에만 건다.
		members.target = new MemberEntity(1L, "admin@kbsec.com", "관리자", "TENANT_ADMIN", true, null);
		members.activeAdminIds = new ArrayList<>(List.of(1L));
		service.changeRole(1, new ChangeMemberRoleRequest("TENANT_ADMIN"), actor, "ip");
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 경쟁_변경으로_조건부_갱신이_빗나가면_409_이고_감사가_남지_않는다() {
		// findById 와 UPDATE 사이에 다른 트랜잭션이 역할을 바꾸면 조건부 갱신(role=이전값)이
		// 0행이 된다 — stale previousRole 로 틀린 감사를 남기는 대신 409 로 충돌을 드러내고,
		// 화면은 새로고침으로 수렴한다(409 규약).
		members.target = new MemberEntity(42L, "op@kbsec.com", "운영자", "OPERATOR", true, null);
		members.updateRoleRows = 0;
		assertThatThrownBy(() -> service.changeRole(
				42, new ChangeMemberRoleRequest("READ_ONLY"), actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.ROLE_CONFLICT));
		assertThat(actionLog.entries).isEmpty();
	}

	@Test
	void 역할_변경_요청_본문_없음은_400_이다() {
		members.target = new MemberEntity(42L, "op@kbsec.com", "운영자", "OPERATOR", true, null);
		assertThatThrownBy(() -> service.changeRole(42, null, actor, "ip"))
				.isInstanceOfSatisfying(GeneralException.class,
						e -> assertThat(e.getCode()).isEqualTo(ConsoleErrorStatus.INVALID_REQUEST));
		assertThat(members.capturedRole).isNull();
	}

	/** member 원장 대역 — 저장 시 IDENTITY(100~)를 부여해 반환한다. */
	private static final class FakeMembers implements MemberRepository {
		final List<MemberEntity> saved = new ArrayList<>();
		boolean existing = false;
		int deactivateRows = 1;
		int updateRoleRows = 1;
		long capturedRoleTargetId = -1;               // updateRole 호출 캡처
		String capturedRole;
		String capturedExpectedRole;
		MemberEntity target;                          // findById 반환(비활성화·역할변경 대상)
		List<Long> activeAdminIds = new ArrayList<>();  // lockActiveAdminIds 반환(잠긴 활성 관리자)
		private long nextId = 100;

		@Override
		public boolean existsByEmail(String email) {
			return existing;
		}

		@Override
		public MemberEntity save(MemberEntity member) {
			MemberEntity persisted = new MemberEntity(nextId++, member.getEmail(), member.getName(),
					member.getRole(), true, member.getPasswordHash());
			saved.add(persisted);
			return persisted;
		}

		@Override
		public Optional<MemberEntity> findById(Long id) {
			return Optional.ofNullable(target);
		}

		@Override
		public List<Long> lockActiveAdminIds() {
			return List.copyOf(activeAdminIds);
		}

		@Override
		public int deactivate(long id) {
			return deactivateRows;
		}

		@Override
		public int updateRole(long id, String role, String expectedRole) {
			this.capturedRoleTargetId = id;
			this.capturedRole = role;
			this.capturedExpectedRole = expectedRole;
			return updateRoleRows;
		}

		@Override
		public int updateName(long id, String name) {
			return 0;
		}

		@Override
		public List<MemberEntity> findAllOrderByMemberId() {
			return List.copyOf(saved);
		}

		@Override
		public Optional<MemberEntity> findByEmailAndActiveTrue(String email) {
			return Optional.empty();
		}

		@Override
		public long count() {
			return saved.size();
		}

		@Override
		public void touchLastLogin(long id) {
		}
	}

	/** 감사 기록 대역 — DB 없이 record 호출을 캡처한다. */
	private static final class RecordingActionLog extends ConsoleActionLogService {
		record Entry(SessionMember actor, String action, String targetType, String targetId,
				Map<String, Object> detail, String clientIp) {
		}

		final List<Entry> entries = new ArrayList<>();

		RecordingActionLog() {
			super(null, null);
		}

		@Override
		public void record(SessionMember actor, String action, String targetType, String targetId,
				Map<String, Object> detail, String clientIp) {
			entries.add(new Entry(actor, action, targetType, targetId, detail, clientIp));
		}
	}
}
