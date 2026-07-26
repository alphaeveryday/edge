package com.edge.superadmin.dto;

/**
 * 테넌트 생성 요청(ALPHA-121). TenantController POST /api/v1/tenants — env 는 IA 어휘
 * (PoC/Production), admin·email·memo 는 온보딩 기록으로 원장에 보존된다.
 */
public record TenantCreateRequest(String name, String env, String admin, String email,
		String memo) {
}
