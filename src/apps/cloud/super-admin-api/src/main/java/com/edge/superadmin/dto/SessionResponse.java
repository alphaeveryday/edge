package com.edge.superadmin.dto;

import com.edge.superadmin.auth.SessionOperator;

/**
 * 인증 세션 응답 — 로그인·세션 조회가 반환하는 운영자 식별. 와이어 형이라 세션
 * 주체(SessionOperator, auth 도메인)와 별도 타입으로 둔다.
 */
public record SessionResponse(String email, String name) {

	public static SessionResponse from(SessionOperator operator) {
		return new SessionResponse(operator.email(), operator.name());
	}
}
