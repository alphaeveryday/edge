package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.mock.SessionMockStore.SessionUser;

/**
 * 콘솔 세션 응답(ALPHA-513) — 사이드바·헤더가 쓰는 테넌트 컨텍스트. tenant-console-ui
 * session 타입과 1:1 camelCase. mock record(SessionUser)와 형식이 같아도 와이어 형은
 * 별도 타입으로 둔다.
 */
public record SessionUserResponse(String name, String email, String role, String tenantName,
		String tenantDomain, String tenantMark) {

	public static SessionUserResponse from(SessionUser u) {
		return new SessionUserResponse(u.name(), u.email(), u.role(), u.tenantName(),
				u.tenantDomain(), u.tenantMark());
	}
}
