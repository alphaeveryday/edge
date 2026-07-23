package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.mock.AdminSessionMockStore;
import com.edge.superadmin.mock.AdminSessionMockStore.OperatorProfile;
import org.springframework.stereotype.Service;

/**
 * session 화면 mock 표면(ALPHA-515) — 검증만 하고 mock 스토어에 위임한다.
 * 실세션(AuthService·SessionOperator)과 별개다 — DB 연동 시 실세션 조회로 교체.
 */
@Service
public class AdminSessionService {

	private final AdminSessionMockStore store;

	public AdminSessionService(AdminSessionMockStore store) {
		this.store = store;
	}

	public OperatorProfile current() {
		return store.current();
	}

	public void updateDisplayName(String name) {
		if (name == null || name.isBlank()) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		store.updateDisplayName(name);
	}
}
