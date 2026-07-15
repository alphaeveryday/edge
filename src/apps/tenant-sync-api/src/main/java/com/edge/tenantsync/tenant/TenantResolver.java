package com.edge.tenantsync.tenant;

import org.springframework.stereotype.Component;

/**
 * 요청의 테넌트 식별. 계약(docs/contracts/sync-protocol.md): 테넌트는 쿼리·경로·헤더가 아니라
 * **mTLS 인증서-테넌트 바인딩에서만** 도출한다 — 컨트롤러는 요청 파라미터를 절대 테넌트
 * 식별에 쓰지 않는다. 현재는 고정 데모 테넌트이며, sync-auth 티켓에서 인증서 fingerprint
 * → 바인딩 조회로 이 클래스를 직접 재작성한다.
 */
@Component
public class TenantResolver {

	public long resolveTenantId() {
		return 1L; // tenant 시드 마이그레이션 전 임시 값 — tenant 테이블 BIGINT identity
	}
}
