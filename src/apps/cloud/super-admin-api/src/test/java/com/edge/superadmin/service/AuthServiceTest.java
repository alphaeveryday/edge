package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.auth.BootstrapOperators;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * config 부트스트랩 인증의 가장자리를 검증한다: 계정 미설정(로그인 불가 상태)과
 * 빈 입력이 NPE 없이 같은 401 로 수렴하는지가 핵심이다.
 */
class AuthServiceTest {

	@Test
	void 부트스트랩_계정이_없어도_로그인은_401로_수렴한다() throws Exception {
		AuthService service = new AuthService(new BootstrapOperators(null));
		assertThatThrownBy(() -> service.login("operator@edge.local", "pw"))
				.isInstanceOf(GeneralException.class);
	}

	@Test
	void 빈_입력은_대조_없이_401이다() throws Exception {
		AuthService service = new AuthService(new BootstrapOperators(List.of(
				new BootstrapOperators.Operator("operator@edge.local", "EDGE 운영팀", "pw"))));
		assertThatThrownBy(() -> service.login(" ", "pw")).isInstanceOf(GeneralException.class);
		assertThatThrownBy(() -> service.login("operator@edge.local", null))
				.isInstanceOf(GeneralException.class);
	}

	@Test
	void 비밀번호_env_미주입_계정은_비활성이다() throws Exception {
		// 공개 엣지라 기본 비밀번호를 커밋하지 않는다 — env 미설정 배포는 계정이
		// 비활성으로 남아 로그인 불가로 닫힌 채 떠야 한다(fail-closed).
		AuthService service = new AuthService(new BootstrapOperators(List.of(
				new BootstrapOperators.Operator("operator@edge.local", "EDGE 운영팀", ""))));
		assertThatThrownBy(() -> service.login("operator@edge.local", ""))
				.isInstanceOf(GeneralException.class);
		assertThatThrownBy(() -> service.login("operator@edge.local", "demo-operator-1"))
				.isInstanceOf(GeneralException.class);
	}
}
