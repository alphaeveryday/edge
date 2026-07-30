package com.edge.tenantsync.controller;

import com.edge.common.apipayload.ApiResponse;
import com.edge.common.exception.GeneralException;
import com.edge.tenantsync.dto.EventBundle;
import com.edge.tenantsync.error.SyncErrorStatus;
import com.edge.tenantsync.service.SyncBundleService;
import com.edge.tenantsync.tenant.TenantResolver;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Optional;

/**
 * Sync Agent 가 Pull 하는 표면 — GET /api/v1/sync/bundle. 엔드포인트 계약은
 * docs/contracts/sync-protocol.md "엔드포인트 계약(확정)" 절이 SSOT 다.
 * HTTP 관심사만 담당한다: 파라미터 검증. 성공은 항상 200 공통 응답 포맷(ApiResponse) —
 * 신규 없음은 result 필드 생략(ADR-0042, 204 폐지 — 빈 응답도 형상을 증명해 fail-silent 차단).
 */
@RestController
public class SyncBundleController {

	private static final int LIMIT_DEFAULT = 100;
	private static final int LIMIT_MAX = 500;

	private final SyncBundleService syncBundleService;
	private final TenantResolver tenantResolver;

	public SyncBundleController(SyncBundleService syncBundleService, TenantResolver tenantResolver) {
		this.syncBundleService = syncBundleService;
		this.tenantResolver = tenantResolver;
	}

	@GetMapping("/api/v1/sync/bundle")
	public ApiResponse<EventBundle> pull(
			@RequestParam("after") long after,
			@RequestParam(value = "limit", defaultValue = "" + LIMIT_DEFAULT) int limit) {

		// 401·403·410 외 4xx 는 계약 위반(버그)로 간주해 재시도 없이 표면화 — fail-loud.
		// 에러는 공통 응답(ApiResponse)로 나간다 — ExceptionAdvice.
		if (after < 0) {
			throw new GeneralException(SyncErrorStatus.INVALID_AFTER);
		}
		if (limit < 1 || limit > LIMIT_MAX) {
			throw new GeneralException(SyncErrorStatus.INVALID_LIMIT);
		}

		Optional<EventBundle> bundle =
				syncBundleService.pull(tenantResolver.resolveTenantId(), after, limit);

		// 신규 없음 = result 생략(@JsonInclude NON_NULL) — 빈 응답도 isSuccess 형상을 실어
		// 소비자가 자기 인증할 수 있게 한다(ADR-0042).
		return ApiResponse.onSuccess(bundle.orElse(null));
	}
}
