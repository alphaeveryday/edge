package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.mock.MemberMockStore.Member;

/**
 * 사용자·권한 응답(ALPHA-513) — tenant-console-ui users 타입과 1:1 camelCase.
 * mock record(Member)와 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record MemberResponse(long id, String name, String email, String role, String status,
		String lastLogin) {

	public static MemberResponse from(Member m) {
		return new MemberResponse(m.id(), m.name(), m.email(), m.role(), m.status(), m.lastLogin());
	}
}
