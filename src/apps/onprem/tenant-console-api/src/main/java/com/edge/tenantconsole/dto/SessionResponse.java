package com.edge.tenantconsole.dto;

import com.edge.tenantconsole.auth.SessionMember;
import tools.jackson.databind.PropertyNamingStrategies;
import tools.jackson.databind.annotation.JsonNaming;

/**
 * 인증 세션 응답 — 로그인·세션 조회가 반환하는 주체 식별. 필드는 기존 검수 표면과
 * 같은 snake_case. 세션 주체(SessionMember, auth 도메인)와 형식이 같아도 와이어 형은
 * 별도 타입으로 둔다.
 */
@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public record SessionResponse(long memberId, String email, String name, String role) {

	public static SessionResponse from(SessionMember member) {
		return new SessionResponse(member.memberId(), member.email(), member.name(), member.role());
	}
}
