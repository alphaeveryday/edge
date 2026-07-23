package com.edge.tenantconsole.mock;

import org.springframework.stereotype.Component;

/**
 * session 도메인 mock 데이터 — 로그인 사용자·테넌트 컨텍스트(KB증권 데모). 실세션
 * (SessionMember)과 별개의 화면용 mock 이다 — DB 연동 시 service 의 이 스토어
 * 호출부를 실세션·테넌트 조회로 교체한다(ALPHA-513).
 */
@Component
public class SessionMockStore {

	public record SessionUser(String name, String email, String role, String tenantName,
			String tenantDomain, String tenantMark) {
	}

	private SessionUser user = new SessionUser(
			"조영서", "youngseo.cho@kbsec.com", "Admin", "KB증권", "kbsec.com", "KB");

	public synchronized SessionUser current() {
		return user;
	}

	public synchronized void updateDisplayName(String name) {
		user = new SessionUser(name, user.email(), user.role(), user.tenantName(),
				user.tenantDomain(), user.tenantMark());
	}
}
