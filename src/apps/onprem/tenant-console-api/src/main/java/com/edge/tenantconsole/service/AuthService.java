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
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionOperations;

import java.util.Locale;

/**
 * 데모 자체 계정 인증(ADR-0025 하이브리드의 데모 경로): 이메일+비밀번호 →
 * 역할이 실린 SessionMember. 운영 SSO/AD 경로는 같은 SessionMember 로 수렴하는
 * 별도 진입점으로 후속 구현된다(실계약 시점).
 *
 * 의도적 생략(데모 범위 — permission-matrix.md 인증 절에 명시): 로그인
 * 레이트리밋·계정 잠금은 온프렘 내부망 전제의 데모 경로라 구현하지 않는다.
 */
@Service
public class AuthService {

	private static final Logger log = LoggerFactory.getLogger(AuthService.class);

	private final MemberRepository memberRepository;
	private final BootstrapAccounts bootstrapAccounts;
	// 시드 원자성용 — @Transactional 자기 호출은 프록시를 우회하므로 쓰지 않는다.
	private final TransactionOperations transaction;
	private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();
	// 미존재 이메일에도 같은 비용의 BCrypt 비교를 수행하기 위한 더미 해시 —
	// 응답 시간으로 계정 존재 여부가 새는 것(타이밍 오라클)을 막는다.
	private final String timingDummyHash = new BCryptPasswordEncoder().encode("timing-dummy");

	private volatile boolean bootstrapDone = false;

	public AuthService(MemberRepository memberRepository, BootstrapAccounts bootstrapAccounts,
			TransactionOperations transaction) {
		this.memberRepository = memberRepository;
		this.bootstrapAccounts = bootstrapAccounts;
		this.transaction = transaction;
	}

	/**
	 * 로그인 검증 — 실패 사유(미존재·비활성·비밀번호 불일치·SSO 전용 계정)를
	 * 구분하지 않고 같은 401 로 답하며, 미존재 이메일에도 더미 BCrypt 비교를
	 * 수행해 시간 차이도 만들지 않는다(계정 존재 여부 비노출).
	 */
	public SessionMember login(String email, String password) {
		if (email == null || email.isBlank() || password == null || password.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.LOGIN_INVALID);
		}
		ensureBootstrapped();
		Member member = memberRepository.findActiveByEmail(normalize(email)).orElse(null);
		// password_hash NULL = SSO 전용 계정(데모 로컬 로그인 불가) — 미존재와 같은 경로.
		String hash = member == null || member.passwordHash() == null
				? timingDummyHash : member.passwordHash();
		boolean matched = encoder.matches(password, hash);
		if (member == null || member.passwordHash() == null || !matched) {
			throw new GeneralException(ConsoleErrorStatus.LOGIN_INVALID);
		}
		memberRepository.touchLastLogin(member.memberId());
		return new SessionMember(member.memberId(), member.email(), member.name(), member.role());
	}

	/**
	 * 데모 부트스트랩 — 설정 결함(role 오타·중복 이메일 = CHECK/UNIQUE 위반)은
	 * 재시도로 낫지 않으므로 삼키지 않고 그대로 던져 기동을 실패시킨다(Rule 12
	 * fail-loud — 인증 불가 상태를 숨기지 않음). 그 밖의 DB 문제(연결 실패 등 일시
	 * 미가용 — CannotGetJdbcConnectionException 도 NonTransient 계층이라 계열이 아닌
	 * 결함 유형으로 판별한다)는 에러 로그 후 기동을 유지하고 첫 로그인 때 재시도한다.
	 */
	@EventListener(ApplicationReadyEvent.class)
	public void bootstrap() {
		try {
			ensureBootstrapped();
		} catch (DataIntegrityViolationException e) {
			throw e;
		} catch (DataAccessException e) {
			log.error("부트스트랩 시드 실패 — DB 일시 미가용. 다음 로그인 시도에서 재시도한다", e);
		}
	}

	private void ensureBootstrapped() {
		if (bootstrapDone) {
			return;
		}
		synchronized (this) {
			if (bootstrapDone) {
				return;
			}
			bootstrapIfEmpty();
			bootstrapDone = true;
		}
	}

	/**
	 * member 0건일 때만 시드한다(재기동 멱등). 전 계정을 한 트랜잭션으로 넣어
	 * 부분 시드(관리자만 생기고 검수자 누락)가 고착되는 것을 막는다. 시드도
	 * 계정도 없으면 로그인 불가 상태이므로 경고를 남긴다(Rule 12).
	 */
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
		transaction.executeWithoutResult(tx -> {
			for (var account : accounts) {
				memberRepository.insert(normalize(account.email()), account.name(), account.role(),
						encoder.encode(account.password()));
				log.info("bootstrap member 시드: {} ({})", account.email(), account.role());
			}
		});
	}

	private String normalize(String email) {
		return email.trim().toLowerCase(Locale.ROOT);
	}
}
