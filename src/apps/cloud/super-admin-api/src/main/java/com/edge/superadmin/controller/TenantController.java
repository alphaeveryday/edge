package com.edge.superadmin.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.superadmin.mock.TenantMockStore.Tenant;
import com.edge.superadmin.service.TenantService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 테넌트 화면 표면(ALPHA-515) — super-admin-ui tenants 도메인 계약과 1:1.
 * 필드명은 UI 타입과 동일한 camelCase. 콘솔 IA 금지 항목(API Key 관리·사용
 * 중지/재개)은 표면 자체를 두지 않는다(super-admin-console.md).
 */
@RestController
public class TenantController {

	private final TenantService tenantService;

	public TenantController(TenantService tenantService) {
		this.tenantService = tenantService;
	}

	public record TenantCreateRequest(String name, String env, String admin, String email,
			String memo) {
	}

	@GetMapping("/api/v1/tenants")
	public ApiResponse<List<Tenant>> list() {
		return ApiResponse.onSuccess(tenantService.list());
	}

	@PostMapping("/api/v1/tenants")
	public ApiResponse<Void> create(@RequestBody(required = false) TenantCreateRequest request) {
		if (request == null) {
			tenantService.create(null, null, null, null);
		} else {
			// memo 는 UI 입력엔 있으나 mock 목록 표시에 없다 — DB 연동 시 보존 대상.
			tenantService.create(request.name(), request.env(), request.admin(), request.email());
		}
		return ApiResponse.onSuccess(null);
	}
}
