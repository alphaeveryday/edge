package com.edge.superadmin.dto;

/**
 * 테넌트 생성 요청. TenantController POST /api/v1/tenants. memo 는 UI 입력엔 있으나
 * mock 목록 표시에 없어 서비스로 전달되지 않는다 — DB 연동 시 보존 대상.
 */
public record TenantCreateRequest(String name, String env, String admin, String email,
		String memo) {
}
