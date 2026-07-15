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
        'KODEX 200은 오늘 전일 대비 0.92% 하락했습니다.\n\n' +
        '미국 6월 CPI가 예상치를 웃돌며 금리 인하 기대가 후퇴한 가운데, 지수 비중이 큰 반도체 대형주가 동반 약세를 보인 것이 하락의 주된 원인으로 분석됩니다.\n\n' +
        '· 삼성전자(−1.9%)·SK하이닉스(−2.4%) 등 반도체 대형주가 필라델피아 반도체지수 급락(−3.1%) 영향으로 동반 하락하며 지수 낙폭의 절반 이상을 설명합니다.\n' +
        '· 미국 6월 소비자물가가 전년 대비 3.4%로 시장 예상(3.1%)을 상회, 원/달러 환율이 8.2원 상승하고 외국인이 코스피200 선물을 4,120억원 순매도했습니다.\n' +
        '· 외국인 현물 순매도가 5거래일 연속(당일 −2,860억원) 이어지며 낙폭을 키웠습니다.\n' +
        '· 반면 LG에너지솔루션(+2.1%)이 북미 신규 수주 공시에 반등하며 지수 낙폭을 일부 상쇄했습니다.',
      confidence_level: 'MEDIUM',
      counter_factors: ['LG에너지솔루션의 수주 공시 반등은 반대 방향 요인'],
      evidences: [
        {
          kind: 'NEWS',
          title: '미국 6월 CPI 3.4%로 예상 상회',
          source: '연합인포맥스',
          published_at: '2026-07-15T08:30:00+09:00',
        },
        {
          kind: 'NEWS',
          title: '필라델피아 반도체지수 3.1% 급락',
          source: '한국경제',
          published_at: '2026-07-15T07:05:00+09:00',
        },
      ],
      disclaimer: DISCLAIMER,
      published_at: '2026-07-15T15:32:00+09:00',
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
        // 5xx 경로 재현용 — 계약의 폴백 문구 처리(증권사 백엔드 권장)를 화면에서 확인한다
        if (etfTicker === '999999') {
          resolve({ status: 500, body: null });
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
