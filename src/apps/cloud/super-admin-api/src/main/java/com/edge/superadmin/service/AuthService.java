package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.auth.BootstrapOperators;
import com.edge.superadmin.auth.SessionOperator;
import com.edge.superadmin.error.AdminErrorStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;

import java.util.Locale;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 데모 운영자 인증(config 부트스트랩 계정): 이메일+비밀번호 → SessionOperator.
 * DB 미배선 단계라 계정은 application.yaml 에서만 온다 — 기동 시 BCrypt 해시로
 * 변환해 평문을 오래 들고 있지 않는다. 운영자 IdP 연동(ALPHA-474)은 같은
 * SessionOperator 로 수렴하는 별도 진입점으로 후속 구현된다.
 *
 * 의도적 생략(tenant-console AuthService 와 같은 데모 범위): 로그인 레이트리밋·계정
 * 잠금은 allowed_cidrs·WAF(망 제한) 뒤의 운영자 표면이라 구현하지 않는다.
 */
@Service
public class AuthService {

	private static final Logger log = LoggerFactory.getLogger(AuthService.class);

	private record Operator(String name, String passwordHash) {
	}

	private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();
	// 미존재 이메일에도 같은 비용의 BCrypt 비교를 수행하기 위한 더미 해시 —
	// 응답 시간으로 계정 존재 여부가 새는 것(타이밍 오라클)을 막는다.
	private final String timingDummyHash = encoder.encode("timing-dummy");

	private final Map<String, Operator> operators;

	public AuthService(BootstrapOperators bootstrapOperators) {
		// 비밀번호 미주입(빈 값) 계정은 비활성 — 공개 엣지라 기본 비밀번호를 커밋하지
		// 않으므로, env 미설정 배포는 로그인 불가로 닫힌 채 뜬다(fail-closed).
		this.operators = bootstrapOperators.bootstrapOperators().stream()
				.filter(o -> {
					boolean enabled = o.password() != null && !o.password().isBlank();
					if (!enabled) {
						log.warn("부트스트랩 운영자 {} 비활성 — 비밀번호 env 미주입 "
								+ "(ADMIN_BOOTSTRAP_OPERATOR_PASSWORD)", o.email());
					}
					return enabled;
				})
				.collect(Collectors.toUnmodifiableMap(
						o -> normalize(o.email()),
						o -> new Operator(o.name(), encoder.encode(o.password()))));
		if (operators.isEmpty()) {
			// 계정 없음 = 로그인 불가 상태 — 조용히 두지 않는다(Rule 12 fail-loud).
			log.warn("활성 부트스트랩 운영자 계정 없음 — admin 콘솔 로그인이 불가능한 상태다 "
					+ "(admin.auth.bootstrap-operators + 비밀번호 env 필요)");
		}
	}

	/**
	 * 로그인 검증 — 실패 사유(미존재·비밀번호 불일치)를 구분하지 않고 같은 401 로
	 * 답하며, 미존재 이메일에도 더미 BCrypt 비교를 수행해 시간 차이도 만들지
	 * 않는다(계정 존재 여부 비노출).
	 */
	public SessionOperator login(String email, String password) {
		if (email == null || email.isBlank() || password == null || password.isBlank()) {
			throw new GeneralException(AdminErrorStatus.LOGIN_INVALID);
		}
		String normalized = normalize(email);
		Operator operator = operators.get(normalized);
		String hash = operator == null ? timingDummyHash : operator.passwordHash();
		boolean matched = encoder.matches(password, hash);
		if (operator == null || !matched) {
			throw new GeneralException(AdminErrorStatus.LOGIN_INVALID);
		}
		return new SessionOperator(normalized, operator.name());
	}

	private String normalize(String email) {
		return email.trim().toLowerCase(Locale.ROOT);
	}
}
