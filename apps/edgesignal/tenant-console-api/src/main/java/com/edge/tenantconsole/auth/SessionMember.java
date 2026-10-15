package com.edge.tenantconsole.auth;

import java.io.Serializable;

/**
 * 세션에 실리는 인증 주체 — 데모 자체 계정·운영 SSO 가 같은 형태로 수렴하는
 * 세션 추상화다(ADR-0025 하이브리드). role 은 member.role 어휘(4종)를 그대로 쓴다.
 */
public record SessionMember(long memberId, String email, String name, String role)
		implements Serializable {

	/** HttpSession attribute 키 — 필터·컨트롤러가 공유한다. */
	public static final String SESSION_KEY = "SESSION_MEMBER";
}
