package com.edge.tenantconsole.service;

import com.edge.common.exception.GeneralException;
import com.edge.tenantconsole.error.ConsoleErrorStatus;
import com.edge.tenantconsole.mock.SessionMockStore;
import com.edge.tenantconsole.mock.SessionMockStore.SessionUser;
import org.springframework.stereotype.Service;

/**
 * 콘솔 세션 화면 mock 표면(ALPHA-513) — 검증만 하고 mock 스토어에 위임한다.
 * 실세션(AuthService·SessionMember)과 별개다 — DB 연동 시 실세션·테넌트 조회로 교체.
 */
@Service
public class ConsoleSessionService {

	private final SessionMockStore store;

	public ConsoleSessionService(SessionMockStore store) {
		this.store = store;
	}

	public SessionUser current() {
		return store.current();
	}

	public void updateDisplayName(String name) {
		if (name == null || name.isBlank()) {
			throw new GeneralException(ConsoleErrorStatus.INVALID_REQUEST);
		}
		store.updateDisplayName(name);
	}
}
