package com.edge.superadmin.service;

import com.edge.common.exception.GeneralException;
import com.edge.superadmin.error.AdminErrorStatus;
import com.edge.superadmin.mock.TenantMockStore;
import com.edge.superadmin.mock.TenantMockStore.Tenant;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Set;

/**
 * tenants 화면 mock 표면(ALPHA-515) — 검증만 하고 mock 스토어에 위임한다.
 * DB 연동(ALPHA-121 테넌트 생성) 시 스토어 호출부를 tenant 테이블 조회/생성으로 교체.
 */
@Service
public class TenantService {

	/** UI TenantEnv 어휘와 1:1 — 콘솔 IA 의 환경 구분(PoC/Production)의 화면 표기. */
	private static final Set<String> ENVS = Set.of("Prod", "Dev");

	private final TenantMockStore store;

	public TenantService(TenantMockStore store) {
		this.store = store;
	}

	public List<Tenant> list() {
		return store.list();
	}

	public void create(String name, String env, String admin, String email) {
		if (isBlank(name) || isBlank(admin) || isBlank(email) || env == null
				|| !ENVS.contains(env)) {
			throw new GeneralException(AdminErrorStatus.INVALID_REQUEST);
		}
		store.create(name, env, admin, email);
	}

	private boolean isBlank(String value) {
		return value == null || value.isBlank();
	}
}
