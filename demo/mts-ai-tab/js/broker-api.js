// 브로커 API 호출 — 실제 서버는 demo/mock-broker (same-origin 서빙).
// ADR-0053 으로 위젯의 Publication API 직접 호출(동일 오리진 프록시 경유)이 표준이 됐다 —
// 데모의 설명 경로 재배선(proxy-site behavior 분리)은 후속 PR 소관이고, 그때까지
// mock-broker 가 헤더 없는 프록시·상태 매핑·폴백 처리를 맡는다. 이 모듈은 얇은 fetch 래퍼다.
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

  // 차트(일봉·분봉 시계열) 조회 — 콜드 최악 경로는 분봉 4페이지(시간외 720봉): 토큰 3초 +
  // 4페이지 × 3초 + 간격 0.75초 + 한 페이지 429 재시도(응답 대기 3초 + 1.1초 + 재호출 3초)
  // ≈ 19.9초라, 타임아웃은 그 상한보다 길게 둔다(짧으면 정상 경로가 폴백에 갇힌다).
  // 실패는 폴백 형상으로 수렴해 화면이 스켈레톤에 갇히지 않고, 탭 재진입·기간 재선택이 재시도한다.
  // resolve 값: { state: 'OK', data: { candles, ageMs? } } | { state: 'FALLBACK', message }
  function getChart(ticker, interval) {
    const query = new URLSearchParams({ ticker: ticker, interval: interval || '1d' });
    return fetch('/api/broker/chart?' + query.toString(), { signal: AbortSignal.timeout(21000) })
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
