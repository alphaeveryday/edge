package com.edge.tenantsync.tenant;

/**
 * 요청의 테넌트 식별. 계약(docs/contracts/sync-protocol.md): 테넌트는 쿼리·경로·헤더가 아니라
 * **mTLS 인증서-테넌트 바인딩에서만** 도출한다 — 파라미터로 받으면 인가 검증과 별개의 신뢰
 * 입력이 생긴다. 이 인터페이스가 그 규칙의 코드 경계다: 컨트롤러는 요청 파라미터를 절대
 * 테넌트 식별에 쓰지 않는다.
 */
public interface TenantResolver {

	long resolveTenantId();
}
