package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.auth.BootstrapAccounts;
import com.edge.tenantconsole.auth.SessionMember;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.repository.MemberRepository;
import com.edge.tenantconsole.repository.MemberRepository.Member;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;

import java.util.Locale;

/**
 * 데모 자체 계정 인증(ADR-0025 하이브리드의 데모 경로): 이메일+비밀번호 →
 * 역할이 실린 SessionMember. 운영 SSO/AD 경로는 같은 SessionMember 로 수렴하는
 * 별도 진입점으로 후속 구현된다(실계약 시점).
 */
@Service
public class AuthService {

	private static final Logger log = LoggerFactory.getLogger(AuthService.class);

	private final MemberRepository memberRepository;
	private final BootstrapAccounts bootstrapAccounts;
	private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();

	public AuthService(MemberRepository memberRepository, BootstrapAccounts bootstrapAccounts) {
		this.memberRepository = memberRepository;
		this.bootstrapAccounts = bootstrapAccounts;
	}

	/**
	 * 로그인 검증 — 실패 사유(미존재·비활성·비밀번호 불일치·SSO 전용 계정)를
	 * 구분하지 않고 같은 401 로 답한다(계정 존재 여부 노출 방지).
	 */
	public SessionMember login(String email, String password) {
		if (email == null || email.isBlank() || password == null || password.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.LOGIN_INVALID);
		}
		Member member = memberRepository.findActiveByEmail(normalize(email))
				.orElseThrow(() -> new GeneralException(ConsoleErrorStatus.LOGIN_INVALID));
		// password_hash NULL = SSO 전용 계정(데모 로컬 로그인 불가).
		if (member.passwordHash() == null || !encoder.matches(password, member.passwordHash())) {
			throw new GeneralException(ConsoleErrorStatus.LOGIN_INVALID);
		}
		memberRepository.touchLastLogin(member.memberId());
		return new SessionMember(member.memberId(), member.email(), member.name(), member.role());
	}

	/**
	 * 데모 부트스트랩 — member 0건일 때만 시드한다(재기동 멱등). 시드도 계정도
	 * 없으면 로그인 불가 상태이므로 조용히 넘어가지 않고 경고를 남긴다(Rule 12).
	 * 기동 시점 DB 미가용은 에러 로그 후 기동을 유지한다 — 어차피 DB 없인 로그인이
	 * 401 로 실패하고, 시드는 DB 복구 후 재기동에서 수행된다.
	 */
	@EventListener(ApplicationReadyEvent.class)
	public void bootstrap() {
		try {
			bootstrapIfEmpty();
		} catch (org.springframework.dao.DataAccessException e) {
			log.error("부트스트랩 시드 실패 — DB 미가용. 복구 후 재기동 전까지 데모 로그인 불가", e);
		}
	}

	void bootstrapIfEmpty() {
		if (memberRepository.count() > 0) {
			return;
		}
		var accounts = bootstrapAccounts.bootstrapAccounts();
		if (accounts.isEmpty()) {
			log.warn("member 0건 + 부트스트랩 계정 미설정 — 콘솔 로그인이 불가능한 상태다 "
					+ "(console.auth.bootstrap-accounts 또는 SSO 모드 필요)");
			return;
		}
		for (var account : accounts) {
			memberRepository.insert(normalize(account.email()), account.name(), account.role(),
					encoder.encode(account.password()));
			log.info("bootstrap member 시드: {} ({})", account.email(), account.role());
		}
	}

	private String normalize(String email) {
		return email.trim().toLowerCase(Locale.ROOT);
	}
}
