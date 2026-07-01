package com.edge.gateway.tenant;

import org.springframework.stereotype.Component;

/**
 * 스텁 테넌트 리졸버 — embed key <b>검증 없이</b> 고정 TenantContext 반환.
 * M2에서 Public Embed Key 검증/거부(S055~057)·Org/App 식별(S058~063)로 대체.
 */
@Component
public class StubTenantResolver {

    public TenantContext resolve(String embedKey) {
        return new TenantContext("org_demo_0001", "app_demo_0001", embedKey);
    }
}
