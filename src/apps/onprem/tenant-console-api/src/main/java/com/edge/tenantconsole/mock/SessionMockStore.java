package com.edge.tenantconsole.mock;

import org.springframework.stereotype.Component;

/**
 * session 도메인 mock 데이터 — 로그인 사용자·테넌트 컨텍스트(KB증권 데모). 실세션
 * (SessionMember)과 별개의 화면용 mock 이다 — 앱 전역 singleton 이라 사용자·세션별
 * 격리가 없다(한 세션의 이름 변경이 전 세션에 보인다 — mock 단계의 의도적
 * 트레이드오프). DB 연동 시 service 의 이 스토어 호출부를 실세션·테넌트 조회로
 * 교체한다(ALPHA-513).
 */
@Component
public class SessionMockStore {

	public record SessionUser(String name, String email, String role, String tenantName,
			String tenantDomain, String tenantMark) {
	}

	// role 필드는 자리표시일 뿐이다 — 실제 응답의 role 은 ConsoleSessionController 가 인증
	// 주체(SessionMember)의 현재 값으로 채운다(ALPHA-119). 여기 값은 출력에 쓰이지 않는다.
	private SessionUser user = new SessionUser(
			"조영서", "youngseo.cho@kbsec.com", "TENANT_ADMIN", "KB증권", "kbsec.com", "KB");

	public synchronized SessionUser current() {
		return user;
	}

	public synchronized void updateDisplayName(String name) {
		user = new SessionUser(name, user.email(), user.role(), user.tenantName(),
				user.tenantDomain(), user.tenantMark());
	}
}
