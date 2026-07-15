// On-Premise Serving API 모킹 — 응답 형상의 근거는 docs/contracts/serving-api.md (ALPHA-366 초안).
// 데모에는 실서버가 없으므로 GET /api/v1/explanations/{etf_ticker} 의 상태·본문을 이 모듈이 재현한다.
// 계약 보장: Published(AUTO_PUBLISHED, APPROVED) 상태의 설명만 응답에 존재한다.
(function () {
  'use strict';

  const DISCLAIMER = '본 내용은 공개 정보 기반의 변동 요인 후보이며 투자 권유가 아닙니다.';

  // trade_date 생략 시 "가장 최근 게시분" — 데모 데이터의 기준일
  const LATEST_TRADE_DATE = '2026-07-15';

  const EXPLANATIONS = {
    '069500': {
      publication_id: 'pub-20260715-069500-01',
      etf: { ticker: '069500', name: 'KODEX 200' },
      trade_date: LATEST_TRADE_DATE,
      summary:
        '반도체 비중 상위 구성종목의 동반 상승이 반영된 것으로 보이는 공개 정보 기반 변동 요인 후보입니다.',
      confidence_level: 'MEDIUM',
      counter_factors: ['환율 변동은 반대 방향 요인'],
      evidences: [
        {
          kind: 'NEWS',
          title: '반도체 수출 반등',
          source: '연합인포맥스',
          published_at: '2026-07-15T08:30:00+09:00',
        },
        {
          kind: 'NEWS',
          title: '외국인 현물 순매수 전환',
          source: '한국경제',
          published_at: '2026-07-15T10:05:00+09:00',
        },
      ],
      disclaimer: DISCLAIMER,
      published_at: '2026-07-15T16:40:00+09:00',
    },
    '091160': {
      publication_id: 'pub-20260715-091160-01',
      etf: { ticker: '091160', name: 'KODEX 반도체' },
      trade_date: LATEST_TRADE_DATE,
      summary:
        '주요 편입 종목의 실적 발표 이후 매수세가 이어진 점이 관찰되는 공개 정보 기반 변동 요인 후보입니다.',
      confidence_level: 'HIGH',
      counter_factors: ['미국 금리 관련 발언은 반대 방향 요인'],
      evidences: [
        {
          kind: 'NEWS',
          title: '반도체 대형주 2분기 실적 시장 예상 상회',
          source: '머니투데이',
          published_at: '2026-07-15T09:10:00+09:00',
        },
      ],
      disclaimer: DISCLAIMER,
      published_at: '2026-07-15T16:40:00+09:00',
    },
    // 노출 가능한 설명이 없는 종목 — 204 경로 재현용 (계약: 정상 상태, body 없음)
    '114800': null,
  };

  const LATENCY_MS = 400;

  // 계약의 상태 시맨틱대로 {status, body}를 돌려준다. 네트워크 왕복처럼 보이게 지연을 준다.
  function getExplanation(etfTicker, tradeDate, headers) {
    return new Promise(function (resolve) {
      setTimeout(function () {
        if (!headers || !headers['X-Customer-Hash'] || !headers['X-Channel']) {
          resolve({ status: 400, body: null });
          return;
        }
        if (tradeDate && !/^\d{4}-\d{2}-\d{2}$/.test(tradeDate)) {
          resolve({ status: 400, body: null });
          return;
        }
        if (!(etfTicker in EXPLANATIONS)) {
          resolve({ status: 404, body: null });
          return;
        }
        const found = EXPLANATIONS[etfTicker];
        if (!found || (tradeDate && tradeDate !== found.trade_date)) {
          resolve({ status: 204, body: null });
          return;
        }
        // 계약: 이 200 응답 시점에 Exposure Log가 자동 기록된다(조회=노출).
        console.log(
          '[serving-api-mock] Exposure Log 기록 — ticker=%s, hash=%s, channel=%s',
          etfTicker,
          headers['X-Customer-Hash'],
          headers['X-Channel']
        );
        resolve({ status: 200, body: found });
      }, LATENCY_MS);
    });
  }

  window.ServingApiMock = { getExplanation: getExplanation };
})();
