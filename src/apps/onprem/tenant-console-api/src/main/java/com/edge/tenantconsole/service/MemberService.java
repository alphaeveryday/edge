package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.mock.MemberMockStore;
import com.edge.tenantconsole.mock.MemberMockStore.Member;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Set;

/**
 * 사용자·권한 mock 표면(ALPHA-513) — 어휘 검증만 하고 mock 스토어에 위임한다.
 * DB 연동(ALPHA-119 사용자 관리) 시 스토어 의존을 repository 로 교체한다.
 */
@Service
public class MemberService {

	// UI 계약의 역할 어휘 — member 원장 어휘(TENANT_ADMIN 등)와의 정합은 ALPHA-119 에서.
	private static final Set<String> ROLES = Set.of("Admin", "Compliance");

	private final MemberMockStore store;

	public MemberService(MemberMockStore store) {
		this.store = store;
	}

	public List<Member> list() {
		return store.list();
	}

	public void invite(String email, String role) {
		if (email == null || email.isBlank() || !email.contains("@") || !ROLES.contains(role)) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		store.invite(email, role);
	}
}
