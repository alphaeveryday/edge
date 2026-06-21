// Analysis API mock client (S046 PoC)
//
// local mock Gateway가 호출하는 "내부 분석 API" 자리표시자다.
// 실제 ML/분석 API 호출이나 분석 DB 조회는 하지 않으며, 요청 symbol로 분석 서버 v1 fixture를 반환한다.
// 추후 실제 분석 API 호출로 교체할 수 있도록 인터페이스(getLatestAnalysis)를 명확히 둔다.

import { normalizeSymbolForMatch } from './gateway-adapter.js';
import { analysisDbV1, DEFAULT_AS_OF } from './fixtures/analysis-response.fixture.js';

// PoC/test 전용: 분석 API 예외 흐름을 검증하기 위한 트리거 symbol.
// 실제 운영 symbol이 아니며, 테스트에서만 사용한다.
const ERROR_TEST_SYMBOLS = new Set(['THROW_ERROR', 'ERROR_TEST']);

export function createEmptyAnalysisResponse(symbol) {
  return {
    request_id: `req_empty_${normalizeSymbolForMatch(symbol) || 'unknown'}`,
    as_of: DEFAULT_AS_OF,
    affected_assets: [],
  };
}

// 요청 symbol과 매칭되는 분석 서버 v1 응답을 찾아 단일 asset 응답으로 반환한다.
// 매칭되는 asset이 없으면 null.
export function findAnalysisFixtureBySymbol(symbol) {
  const target = normalizeSymbolForMatch(symbol);
  if (!target) {
    return null;
  }
  const asset = analysisDbV1.affected_assets.find(
    (item) => item && normalizeSymbolForMatch(item.code) === target,
  );
  if (!asset) {
    return null;
  }
  return {
    request_id: analysisDbV1.request_id,
    as_of: analysisDbV1.as_of,
    affected_assets: [asset],
  };
}

// 내부 분석 API mock 호출 진입점.
// tenantContext는 PoC에서 전달만 받고 권한/DB 조회에는 사용하지 않는다 (실제 운영에서 사용 예정).
export async function getLatestAnalysis({ symbol, tenantContext } = {}) {
  const raw = String(symbol == null ? '' : symbol).trim();

  if (ERROR_TEST_SYMBOLS.has(raw.toUpperCase())) {
    throw new Error(`analysis API mock error (test symbol: ${raw})`);
  }

  // tenantContext는 의도적으로 사용하지 않는다. 실제 분석 API 교체 시 인증/스코프 전달에 쓰인다.
  void tenantContext;

  return findAnalysisFixtureBySymbol(raw) || createEmptyAnalysisResponse(raw);
}
