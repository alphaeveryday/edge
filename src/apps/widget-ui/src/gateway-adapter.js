// Gateway adapter (S049 PoC)
//
// 분석 서버 v1 응답(affected_assets[].summary)을 위젯 표준 응답으로 변환하는 pure function 모음.
// 실제 Gateway 서버/endpoint/Public Embed Key 검증/ML API/분석 DB 조회는 구현하지 않는다.
// 실제 Gateway 레포가 생기면 이 모듈을 Gateway 쪽으로 이동할 수 있도록 부수효과 없는 순수 함수로 작성한다.

export const DEFAULT_DISCLAIMER =
  '본 정보는 투자 참고용이며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.';

export const EMPTY_DISCLAIMER = '해당 종목의 최신 분석 결과가 없습니다.';

export const ERROR_MESSAGE = '위젯 응답 변환 중 문제가 발생했습니다.';

export const DEFAULT_GENERATED_AT = '2026-03-12T15:30:00+09:00';

const DEFAULT_FALLBACK_REASON = '실시간 분석 데이터를 수집할 수 없습니다.';

// PoC용 단순 symbol 매칭 helper.
// symbol canonicalization은 아직 확정되지 않았으며, S049에서는 PoC용 단순 매칭만 제공한다.
// 실제 운영에서는 Gateway adapter 또는 별도 symbol mapping layer에서 처리해야 한다.
export function normalizeSymbolForMatch(symbol) {
  let value = String(symbol == null ? '' : symbol).trim();
  if (!value) {
    return '';
  }
  // vendor prefix 제거: KRX:005930 -> 005930
  value = value.replace(/^[A-Za-z]+:/, '');
  // 거래소 suffix 제거: 005930.KS -> 005930
  value = value.replace(/\.[A-Za-z]+$/, '');
  // 대소문자 차이는 무시한다.
  return value.toUpperCase();
}

export function findMatchingAsset(affectedAssets, symbol) {
  if (!Array.isArray(affectedAssets) || affectedAssets.length === 0) {
    return null;
  }
  const target = normalizeSymbolForMatch(symbol);
  if (!target) {
    return null;
  }
  return (
    affectedAssets.find(
      (asset) => asset && normalizeSymbolForMatch(asset.code) === target,
    ) || null
  );
}

// 분석 API가 title을 제공하면 그대로 pass-through 매핑한다.
// title이 없으면 null로 둔다. summary에서 title을 생성/추론하지 않는다.
// "가격 변동 설명" 같은 기본 라벨은 adapter가 아니라 위젯 UI 렌더링 계층에서 처리한다.
export function buildSummaryCard(asset) {
  return {
    title: asset && asset.title ? asset.title : null,
    description: asset && asset.summary ? asset.summary : '',
  };
}

export function createSuccessResponse(asset, request, options = {}) {
  const summary = asset && asset.summary ? asset.summary : '';
  return {
    status: 'success',
    symbol: requestSymbol(request),
    generatedAt: options.generatedAt || DEFAULT_GENERATED_AT,
    summary,
    cards: summary ? [buildSummaryCard(asset)] : [],
    disclaimer: options.disclaimer || DEFAULT_DISCLAIMER,
    newsLinks: [],
    fallback: {
      isFallback: false,
      reason: null,
      basedAt: null,
    },
  };
}

export function createEmptyResponse(request, options = {}) {
  return {
    status: 'empty',
    symbol: requestSymbol(request),
    generatedAt: options.generatedAt || DEFAULT_GENERATED_AT,
    summary: '',
    cards: [],
    disclaimer: options.emptyDisclaimer || EMPTY_DISCLAIMER,
    newsLinks: [],
    fallback: {
      isFallback: false,
      reason: null,
      basedAt: null,
    },
  };
}

export function createErrorResponse(request, error) {
  const response = {
    status: 'error',
    symbol: requestSymbol(request),
    message: ERROR_MESSAGE,
  };
  if (error && error.message) {
    response.details = error.message;
  }
  return response;
}

// 기존 success/empty widget response를 fallback 응답으로 감싼다.
// 실제 cache/fallback 정책은 아직 구현하지 않는다. summary가 있으면 그대로 유지한다.
export function createFallbackResponse(widgetResponse, reason, basedAt) {
  const base =
    widgetResponse && typeof widgetResponse === 'object' ? widgetResponse : {};
  return {
    ...base,
    status: 'fallback',
    fallback: {
      isFallback: true,
      reason: reason || DEFAULT_FALLBACK_REASON,
      basedAt: basedAt || null,
    },
  };
}

// 분석 서버 v1 응답 -> 위젯 표준 응답 변환 진입점.
export function mapAnalysisToWidgetResponse(analysisResponse, request, options = {}) {
  try {
    if (
      !analysisResponse ||
      typeof analysisResponse !== 'object' ||
      !Array.isArray(analysisResponse.affected_assets)
    ) {
      return createErrorResponse(request, new Error('invalid analysis response shape'));
    }

    const mergedOptions = {
      ...options,
      generatedAt: analysisResponse.as_of || options.generatedAt || DEFAULT_GENERATED_AT,
    };

    const asset = findMatchingAsset(analysisResponse.affected_assets, request && request.symbol);
    if (!asset || !asset.summary) {
      return createEmptyResponse(request, mergedOptions);
    }

    return createSuccessResponse(asset, request, mergedOptions);
  } catch (error) {
    return createErrorResponse(request, error);
  }
}

function requestSymbol(request) {
  return request && request.symbol ? request.symbol : '';
}
