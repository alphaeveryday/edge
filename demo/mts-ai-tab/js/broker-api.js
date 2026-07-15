// 증권사 자체 제작 API 모킹 — 실제 배치에서는 증권사 Backend/API Gateway에서 동작하는 레이어다.
// 계약 원칙(docs/contracts/serving-api.md): MTS 화면은 Serving API를 직접 호출하지 않는다.
// 화면(app.js)은 이 모듈만 호출하고, Serving API 호출·고객 해시·채널 전달은 전부 이 레이어 책임이다.
(function () {
  'use strict';

  // 데모용 고객 해시 — 실제 해시 생성 규칙·salt는 증권사 관리 영역(ADR-0013, 벤더 불관여).
  // 원본 고객 ID/계좌번호는 Serving API로 절대 전달하지 않는다.
  const DEMO_CUSTOMER_HASH = 'demo-c7f3a91b2e';
  const CHANNEL = 'MTS';

  // 계약 권장: 5xx는 폴백 문구로 처리해 설명 미제공이 고객 화면 오류로 보이지 않게 한다.
  const FALLBACK_MESSAGE = 'AI 분석을 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.';
  const NO_DATA_MESSAGE = '이 종목·일자에 대해 제공되는 AI 분석이 아직 없습니다.';

  // 실제 Serving API 엔드포인트가 생기면 이 값을 채워 fetch 경로로 전환한다(형상은 동일).
  const SERVING_BASE_URL = null;

  function callServing(ticker, tradeDate, headers) {
    if (SERVING_BASE_URL) {
      const query = tradeDate ? '?trade_date=' + encodeURIComponent(tradeDate) : '';
      return fetch(SERVING_BASE_URL + '/api/v1/explanations/' + encodeURIComponent(ticker) + query, {
        headers: headers,
      }).then(function (res) {
        if (res.status === 200) {
          return res.json().then(function (body) {
            return { status: 200, body: body };
          });
        }
        return { status: res.status, body: null };
      });
    }
    return window.ServingApiMock.getExplanation(ticker, tradeDate, headers);
  }

  // MTS 화면이 쓰는 유일한 진입점.
  // resolve 값: { state: 'OK', data } | { state: 'NO_DATA', message } | { state: 'FALLBACK', message }
  function getAiAnalysis(ticker, tradeDate) {
    const headers = {
      'X-Customer-Hash': DEMO_CUSTOMER_HASH,
      'X-Channel': CHANNEL,
    };
    return callServing(ticker, tradeDate, headers)
      .then(function (res) {
        if (res.status === 200) {
          return { state: 'OK', data: res.body };
        }
        if (res.status === 204) {
          return { state: 'NO_DATA', message: NO_DATA_MESSAGE };
        }
        if (res.status === 404) {
          return { state: 'NO_DATA', message: '지원하지 않는 종목입니다. (국내 상장 ETF 대상)' };
        }
        return { state: 'FALLBACK', message: FALLBACK_MESSAGE };
      })
      .catch(function () {
        return { state: 'FALLBACK', message: FALLBACK_MESSAGE };
      });
  }

  window.BrokerApi = { getAiAnalysis: getAiAnalysis };
})();
