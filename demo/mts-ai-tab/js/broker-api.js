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
    // 타임아웃 필수 — 응답 없는 pending 이 부트 체인(딥링크 처리)을 무기한 붙들지 않게
    return fetch('/api/broker/quotes', { signal: AbortSignal.timeout(5000) })
      .then(function (res) {
        return res.json();
      })
      .catch(function (err) {
        console.warn('[broker-api] 시세 호출 실패', err);
        return { state: 'FALLBACK', data: null };
      });
  }

  // 차트(일봉 시계열) 조회 — 콜드 최악 경로는 토큰 3초 + 1페이지 3초 + 간격 0.25초 +
  // 2페이지 429 재시도(429 응답 대기 3초 + 1.1초 + 재호출 3초) ≈ 13.35초라, 타임아웃은 그 상한보다
  // 길게 둔다(짧으면 정상 재시도 경로가 폴백에 갇힌다). 실패는 폴백 형상으로 수렴해 화면이
  // 스켈레톤에 갇히지 않고, 폴백 시 탭 재진입이 재시도한다(app.js chartFetched 리셋).
  // resolve 값: { state: 'OK', data: { candles } } | { state: 'FALLBACK', message }
  function getChart(ticker) {
    const query = new URLSearchParams({ ticker: ticker });
    return fetch('/api/broker/chart?' + query.toString(), { signal: AbortSignal.timeout(14000) })
      .then(function (res) {
        return res.json();
      })
      .catch(function (err) {
        console.warn('[broker-api] 차트 호출 실패', err);
        return { state: 'FALLBACK', message: '차트를 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.' };
      });
  }

  window.BrokerApi = { getAiAnalysis: getAiAnalysis, getQuotes: getQuotes, getChart: getChart };
})();
