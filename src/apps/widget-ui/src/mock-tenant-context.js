// Mock tenant/application context (S046 PoC)
//
// 실제 운영에서는 Public Embed Key(embedKey) 검증 결과로 tenantContext를 생성해야 한다.
// 현재 PoC에서는 실제 검증/DB 조회 없이 request 값으로 mock context만 만든다.
//
// 중요: clientId는 debug/logging/consistency check 보조값이며 신뢰 기준(tenant 식별의 source of truth)이 아니다.
// tenant 식별의 신뢰 기준은 embedKey 검증 결과여야 한다 (추후 Gateway 구현 시 결정).

const DEFAULT_APPLICATION_ID = 'app_mts';
const DEFAULT_WIDGET_ID = 'asset-event-impact';

export function createMockTenantContext(request = {}) {
  const source = request && typeof request === 'object' ? request : {};
  const embedKey = source.embedKey ? String(source.embedKey).trim() : null;
  const clientId = source.clientId ? String(source.clientId).trim() : null;
  const widgetId = source.widgetId ? String(source.widgetId).trim() : null;

  return {
    // PoC: organizationId는 clientId를 mock 변환한 값일 뿐 실제 식별자가 아니다.
    organizationId: clientId ? `org_${clientId.replace(/-/g, '_')}` : 'org_unknown',
    applicationId: DEFAULT_APPLICATION_ID,
    widgetId: widgetId || DEFAULT_WIDGET_ID,
    embedKey,
    clientId,
  };
}
