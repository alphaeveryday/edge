// Mock Gateway service (S046 PoC)
//
// local mock Gateway endpoint 내부 흐름을 테스트 가능한 service 함수로 분리한다.
//
//   request body
//   -> mock tenantContext 생성 (createMockTenantContext)
//   -> 내부 분석 API mock 호출 (analysisApiClient.getLatestAnalysis)
//   -> S049 adapter (mapAnalysisToWidgetResponse)
//   -> widget response
//
// 실제 Gateway 서버/Public Embed Key 검증/tenant DB 조회/실제 분석 API 호출은 없다 (추후 Gateway 구현 시 결정).

import { createMockTenantContext } from './mock-tenant-context.js';
import { getLatestAnalysis } from './analysis-api-client.js';
import {
  mapAnalysisToWidgetResponse,
  createErrorResponse,
  DEFAULT_DISCLAIMER,
} from './gateway-adapter.js';

// deps는 테스트 주입용(예: getLatestAnalysis 스텁). 운영 경로에서는 비워 둔다.
export async function handleWidgetAnalysisRequest(requestBody, deps = {}) {
  const fetchAnalysis = deps.getLatestAnalysis || getLatestAnalysis;
  const request = requestBody && typeof requestBody === 'object' ? requestBody : {};
  const symbol = request.symbol ? String(request.symbol).trim() : '';

  if (!symbol) {
    return createErrorResponse(request, new Error('요청에 symbol이 없습니다.'));
  }

  try {
    const tenantContext = createMockTenantContext(request);
    const analysisResponse = await fetchAnalysis({ symbol, tenantContext });
    return mapAnalysisToWidgetResponse(analysisResponse, request, {
      disclaimer: DEFAULT_DISCLAIMER,
    });
  } catch (error) {
    return createErrorResponse(request, error);
  }
}
