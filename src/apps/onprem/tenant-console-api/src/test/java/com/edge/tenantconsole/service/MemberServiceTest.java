package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
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

	/** member 원장 대역 — 저장 시 IDENTITY(100~)를 부여해 반환한다. */
	private static final class FakeMembers implements MemberRepository {
		final List<MemberEntity> saved = new ArrayList<>();
		boolean existing = false;
		int deactivateRows = 1;
		MemberEntity target;                          // findById 반환(비활성화 대상)
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
