// 분석 서버 v1 응답 fixture (S049 PoC 전용)
//
// 분석 서버 v1은 구조화된 factor/score 배열이 아니라
// affected_assets[].summary 형태의 완성된 설명 문장을 제공한다.
// 실제 ML API / 분석 DB 조회는 구현하지 않으며, 아래 값은 PoC 검증용 고정 데이터다.

export const ANALYSIS_V1_SUMMARY =
  '이번 삼성전자 하락은 반도체 규제 뉴스가 가장 크게 작용했어요. 전체 설명 중 절반 이상은 미국의 중국향 반도체 수출 규제 강화로 보는 게 자연스러워요. 삼성전자는 중국 반도체 수요와 장비 규제에 민감하고, 과거 비슷한 규제 뉴스 43건에서도 평균 -1.8% 하락했으며 72%는 같은 하락 방향이었어요. 그다음은 메모리 반도체 수요 전망 하향이에요. 비중은 규제 뉴스의 절반보다 작지만, 삼성전자 이익 기대를 낮추는 보조 악재로 작용했어요. 과거 비슷한 수요 전망 하향 뉴스 31건에서도 평균 -0.9% 하락했어요. 원화 약세는 영향이 있더라도 작게 보는 게 맞아요. 시장 전체나 수출주 전반에는 영향을 줄 수 있지만, 이번 삼성전자 하락을 직접 설명하는 핵심 요인으로 보기엔 근거가 약해요.';

// 분석 서버 v1 기준 시각 (PoC 고정값)
export const DEFAULT_AS_OF = '2026-03-12T15:30:00+09:00';

export const OTHER_ASSET_SUMMARY =
  'SK하이닉스 약세는 메모리 가격 회복 지연 우려가 가장 크게 작용했어요. 외국인 순매도가 동반되며 단기 수급도 약했지만, 핵심 동인은 수요 전망 하향이에요.';

// success용 분석 응답: 005930.KS 매칭
export const successAnalysisResponse = {
  request_id: 'req_20260312_005930_1d',
  as_of: '2026-03-12T15:30:00+09:00',
  affected_assets: [
    {
      code: '005930.KS',
      summary: ANALYSIS_V1_SUMMARY,
    },
  ],
};

// empty용 분석 응답: affected_assets가 비어 있음
export const emptyAnalysisResponse = {
  request_id: 'req_20260312_empty_1d',
  as_of: '2026-03-12T15:30:00+09:00',
  affected_assets: [],
};

// summary empty 케이스: asset은 매칭되지만 summary가 비어 있음
export const emptySummaryAnalysisResponse = {
  request_id: 'req_20260312_005930_nosum',
  as_of: '2026-03-12T15:30:00+09:00',
  affected_assets: [
    {
      code: '005930.KS',
      summary: '',
    },
  ],
};

// 다른 symbol 케이스: 000660.KS (SK하이닉스)
export const otherSymbolAnalysisResponse = {
  request_id: 'req_20260312_000660_1d',
  as_of: '2026-03-12T15:30:00+09:00',
  affected_assets: [
    {
      code: '000660.KS',
      summary: OTHER_ASSET_SUMMARY,
    },
  ],
};

// local mock Gateway endpoint가 조회하는 PoC 분석 DB.
// 실제 분석 DB 조회가 아니라, 여러 종목 fixture를 한 응답으로 합쳐 둔 고정 데이터다.
export const analysisDbV1 = {
  request_id: 'req_20260312_db_1d',
  as_of: '2026-03-12T15:30:00+09:00',
  affected_assets: [
    {
      code: '005930.KS',
      summary: ANALYSIS_V1_SUMMARY,
    },
    {
      code: '000660.KS',
      summary: OTHER_ASSET_SUMMARY,
    },
  ],
};

// future-compatible 케이스: 분석 API가 title을 제공하는 경우.
// 현재 v1 공식 기준은 summary 중심이며, title은 future-compatible optional field다.
// adapter는 title이 있으면 cards[0].title로 그대로 pass-through 매핑한다.
export const analysisResponseWithTitleFixture = {
  request_id: 'req_20260312_005930_1d',
  as_of: '2026-03-12T15:30:00+09:00',
  affected_assets: [
    {
      code: '005930.KS',
      title: '반도체 규제 이슈 영향',
      summary: ANALYSIS_V1_SUMMARY,
    },
  ],
};
