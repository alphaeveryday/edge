package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.model.Member;

import java.time.OffsetDateTime;

/**
 * 사용자·권한 응답 — tenant-console-ui users 타입과 1:1 camelCase(ALPHA-513 계약 유지,
 * ALPHA-119 에서 mock → member 원장 실데이터로 백엔드만 교체). status 는 원장 is_active
 * 를 UI 표기로 매핑한다(활성=ACTIVE·비활성=INACTIVE — 관리자 직접 등록이라 INVITED 없음).
 * lastLogin 은 last_login_at ISO-8601(미로그인은 null).
 */
public record MemberResponse(long id, String name, String email, String role, String status,
		String lastLogin) {

	public static MemberResponse from(Member m) {
		return new MemberResponse(m.memberId(), m.name(), m.email(), m.role(),
				m.active() ? "ACTIVE" : "INACTIVE", isoOrNull(m.lastLoginAt()));
	}

	private static String isoOrNull(OffsetDateTime at) {
		return at == null ? null : at.toString();
	}
}
