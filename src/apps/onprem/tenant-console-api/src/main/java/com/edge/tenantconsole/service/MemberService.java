package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.dto.CreateMemberRequest;
import com.edge.tenantconsole.entity.MemberEntity;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.model.Member;
import com.edge.tenantconsole.repository.MemberRepository;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * 콘솔 사용자 관리(ALPHA-119) — member 원장 위 등록·목록·비활성화. 관리자 직접
 * 등록만(초대·재설정 메일 흐름 없음, ADR-0025)이라 중간 상태(INVITED)는 두지 않고
 * is_active 불리언만 쓴다. role 어휘는 원장 4종(ck_member_role)과 동일하다.
 *
 * 변경(등록·비활성화)은 감사 기록 대상이므로 상태 변경과 console_action_log append 를
 * 한 트랜잭션(@Transactional)으로 묶는다 — 감사에 실패하면 변경도 함께 롤백해
 * "기록 없는 변경"을 만들지 않는다(감사 요건, Rule 12).
 */
@Service
public class MemberService {

	/** 원장 role 어휘(스키마 ck_member_role 과 동일) — 어휘 밖 role 은 400 으로 막는다. */
	static final Set<String> ROLES = Set.of(
			"TENANT_ADMIN", "COMPLIANCE_REVIEWER", "OPERATOR", "READ_ONLY");

	private final MemberRepository memberRepository;
	private final ConsoleActionLogService actionLog;
	private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();

	public MemberService(MemberRepository memberRepository, ConsoleActionLogService actionLog) {
		this.memberRepository = memberRepository;
		this.actionLog = actionLog;
	}

	public List<Member> list() {
		return memberRepository.findAllOrderByMemberId().stream().map(Member::from).toList();
	}

	/**
	 * 등록 — 이메일·이름은 비어 있지 않고 role 은 4종이어야 한다(위반은 400). password
	 * 가 있으면 데모 자체 계정(BCrypt), 없으면 SSO 전용(password_hash NULL). 이메일
	 * 중복은 409 — 사전 검사 + UNIQUE 제약 위반(경쟁) 백스톱으로 이중 방어한다.
	 */
	@Transactional
	public Member register(CreateMemberRequest request, SessionMember actor, String clientIp) {
		if (request == null) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		String email = normalize(request.email());
		String name = request.name() == null ? null : request.name().trim();
		String role = request.role();
		// role == null 을 ROLES.contains 앞에서 먼저 막는다 — Set.of 의 contains(null)
		// 은 NPE 라, 누락 시 400 대신 500 이 나가는 우회를 차단한다.
		if (email == null || email.isEmpty() || !email.contains("@")
				|| name == null || name.isEmpty() || role == null || !ROLES.contains(role)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		if (memberRepository.existsByEmail(email)) {
			throw new GeneralException(ConsoleErrorStatus.DUPLICATE_MEMBER_EMAIL);
		}
		String rawPassword = request.password();
		boolean hasPassword = rawPassword != null && !rawPassword.isBlank();
		// BCrypt 는 72바이트까지만 처리한다 — 초과 시 encode 가 IllegalArgumentException(→500)
		// 이라, 인코딩 전에 UTF-8 바이트 길이로 걸러 400 으로 응답한다(malformed→500 우회 차단).
		if (hasPassword && rawPassword.getBytes(StandardCharsets.UTF_8).length > 72) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		String passwordHash = hasPassword ? encoder.encode(rawPassword) : null;
		MemberEntity saved;
		try {
			saved = memberRepository.save(new MemberEntity(email, name, role, passwordHash));
		} catch (DataIntegrityViolationException e) {
			// 사전 검사와 저장 사이의 경쟁(uq_member_email 위반) — 409 로 수렴.
			throw new GeneralException(ConsoleErrorStatus.DUPLICATE_MEMBER_EMAIL);
		}
		Member member = Member.from(saved);
		actionLog.record(actor, "MEMBER_REGISTERED", "MEMBER", String.valueOf(member.memberId()),
				Map.of("email", email, "role", role), clientIp);
		return member;
	}

	/**
	 * 비활성화 — is_active=false 토글. 대상 미존재는 404. 마지막 활성 관리자(자기 자신
	 * 포함)는 막는다(409) — 비활성화하면 사용자 관리 권한을 가진 계정이 사라지는데 재활성
	 * 엔드포인트도, 부트스트랩 복구(member 존재 시 시드 건너뜀)도 없어 테넌트가 잠긴다.
	 * 성공 시 MEMBER_DEACTIVATED 감사.
	 */
	@Transactional
	public void deactivate(long memberId, SessionMember actor, String clientIp) {
		MemberEntity target = memberRepository.findById(memberId)
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.MEMBER_NOT_FOUND));
		// 관리자 대상이면 활성 관리자 집합을 FOR UPDATE 로 잠근 뒤, 잠긴 집합의 현재 멤버십으로
		// 판정한다 — target.isActive() 는 락 이전 값이라, 같은 관리자를 동시 비활성화할 때 먼저
		// 커밋된 뒤 남은 트랜잭션이 stale 값으로 오판(이미 비활성인데 LAST_ADMIN)하는 걸 막는다.
		// target 이 잠긴 활성 집합에 있고 그게 유일하면 마지막 관리자다(없으면 이미 비활성 = 멱등).
		if ("TENANT_ADMIN".equals(target.getRole())) {
			List<Long> activeAdmins = memberRepository.lockActiveAdminIds();
			if (activeAdmins.contains(target.getMemberId()) && activeAdmins.size() <= 1) {
				throw new GeneralException(ConsoleErrorStatus.LAST_ADMIN);
			}
		}
		if (memberRepository.deactivate(memberId) == 0) {
			throw new GeneralException(ConsoleErrorStatus.MEMBER_NOT_FOUND);
		}
		actionLog.record(actor, "MEMBER_DEACTIVATED", "MEMBER", String.valueOf(memberId),
				null, clientIp);
	}

	private static String normalize(String email) {
		return email == null ? null : email.trim().toLowerCase(Locale.ROOT);
	}
}
