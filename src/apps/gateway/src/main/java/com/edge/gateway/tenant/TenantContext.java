package com.edge.gateway.tenant;

/**
 * 요청을 처리할 테넌트 컨텍스트. 스텁 단계에선 고정값이며, 위젯 표준 응답에는
 * (계약상) 노출하지 않는다. M2에서 embed key 검증·Org/App 식별로 실제 생성(S058~063).
 */
public record TenantContext(String organizationId, String applicationId, String embedKey) {
}
