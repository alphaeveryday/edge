import { describe, expect, it } from 'vitest';
import { createMockTenantContext } from '../src/mock-tenant-context.js';

describe('S046 mock tenant context', () => {
  it('request의 embedKey, clientId, widgetId로 tenantContext를 만든다', () => {
    const context = createMockTenantContext({
      embedKey: 'pub_demo_1234',
      clientId: 'demo-sec',
      widgetId: 'asset-event-impact',
    });

    expect(context).toMatchObject({
      organizationId: 'org_demo_sec',
      applicationId: 'app_mts',
      widgetId: 'asset-event-impact',
      embedKey: 'pub_demo_1234',
      clientId: 'demo-sec',
    });
  });

  it('clientId가 없어도 context 생성이 가능하다 (clientId는 신뢰 기준이 아니라 보조값)', () => {
    const context = createMockTenantContext({
      embedKey: 'pub_demo_1234',
      widgetId: 'asset-event-impact',
    });

    expect(context.organizationId).toBe('org_unknown');
    expect(context.clientId).toBeNull();
    // 신뢰 기준은 embedKey이며, clientId 부재가 context 생성을 막지 않는다.
    expect(context.embedKey).toBe('pub_demo_1234');
  });

  it('embedKey가 tenant 식별 기준 후보로 전달된다 (PoC에서는 검증하지 않음)', () => {
    const context = createMockTenantContext({ embedKey: 'pub_other_9999', clientId: 'other-sec' });
    expect(context.embedKey).toBe('pub_other_9999');
    expect(context.organizationId).toBe('org_other_sec');
  });
});
