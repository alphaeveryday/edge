// 증권사 자체 제작 API 호출 — 실제 서버는 demo/mock-broker (same-origin 서빙).
// 계약 원칙(docs/contracts/publication-api.md): MTS 화면은 Publication API를 직접 호출하지 않는다.
// Publication API 호출·고객 해시·채널 부착·상태 매핑은 전부 mock-broker(서버) 책임이고,
// 이 모듈은 브로커 API 를 fetch 하는 얇은 래퍼다.
(function () {
  'use strict';

  const FALLBACK_MESSAGE = 'AI 분석을 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.';

  // MTS 화면이 쓰는 유일한 진입점.
  // resolve 값: { state: 'OK', data } | { state: 'NO_DATA', message } | { state: 'FALLBACK', message }
  function getAiAnalysis(ticker, tradeDate) {
    const query = new URLSearchParams({ ticker: ticker });
    if (tradeDate) {
      query.set('trade_date', tradeDate);
    }
    return fetch('/api/broker/ai-analysis?' + query.toString())
      .then(function (res) {
        return res.json();
      })
      .catch(function (err) {
        console.warn('[broker-api] 증권사 API 호출 실패', err);
        return { state: 'FALLBACK', message: FALLBACK_MESSAGE };
      });
  }

  // 시세(지수·관심종목) 조회 — 서버가 외부 소스 실패·키 미설정 시 스냅샷 폴백을 담아 항상 200 으로 준다.
  // resolve 값: { state: 'OK'|'FALLBACK', data: { indices, stocks } } | { state: 'FALLBACK', data: null }
  function getQuotes() {
    return fetch('/api/broker/quotes')
      .then(function (res) {
        return res.json();
      })
      .catch(function (err) {
        console.warn('[broker-api] 시세 호출 실패', err);
        return { state: 'FALLBACK', data: null };
      });
  }

  window.BrokerApi = { getAiAnalysis: getAiAnalysis, getQuotes: getQuotes };
})();
