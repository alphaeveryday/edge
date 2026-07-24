package com.edge.superadmin.dto;

import com.edge.superadmin.mock.TenantMockStore.Tenant;

import java.util.List;

/**
 * 테넌트 목록 응답. 필드는 super-admin-ui tenants 타입과 동일한 camelCase. mock
 * 스토어 record(Tenant)와 형식이 같아도 와이어 형은 별도 타입으로 둔다.
 */
public record TenantResponse(String id, String name, String domain, String env, String status,
		String admin, String email, String created, String lastSync, String lastSyncAbs,
		int calls, int errors, List<Integer> bars) {

	public static TenantResponse from(Tenant t) {
		return new TenantResponse(t.id(), t.name(), t.domain(), t.env(), t.status(), t.admin(),
				t.email(), t.created(), t.lastSync(), t.lastSyncAbs(), t.calls(), t.errors(),
				t.bars());
	}
}
