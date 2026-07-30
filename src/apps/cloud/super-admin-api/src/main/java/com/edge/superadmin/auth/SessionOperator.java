package com.edge.superadmin.auth;

import java.io.Serializable;

/**
 * 세션에 실리는 인증 주체(벤더 운영자) — 데모 부트스트랩 계정·운영 IdP(ALPHA-474)가
 * 같은 형태로 수렴하는 세션 추상화다(tenant-console SessionMember 와 같은 결).
 * super-admin 은 운영자 단일 역할이라 role 필드를 두지 않는다 — 역할 분화가
 * 생기면 tenant-console 처럼 role 을 세션에 싣고 필터 RULES 에 역할을 더한다.
 */
public record SessionOperator(String email, String name) implements Serializable {

	/** HttpSession attribute 키 — 필터·컨트롤러가 공유한다. */
	public static final String SESSION_KEY = "SESSION_OPERATOR";
}
