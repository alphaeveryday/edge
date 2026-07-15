package com.edge.sync.tenant;

import org.springframework.stereotype.Component;

/**
 * 스텁 — 고정 데모 테넌트. mTLS 인증서 fingerprint → 테넌트 바인딩 조회(요청별 인가 검증,
 * docs/contracts/sync-auth.md)는 sync-auth 후속 티켓에서 이 구현을 교체한다.
 */
@Component
public class FixedTenantResolver implements TenantResolver {

	@Override
	public String resolveTenantId() {
		return "t-demo";
	}
}
