// 데이터 호출 계층 — 설명 조회는 Publication API 직접 호출(ADR-0053, 동일 오리진 경로:
// 박스에서는 CloudFront behavior, 로컬에서는 mock-broker 의 무변형 passthrough 가 /api/v1/*
// 를 publication-api 로 보낸다). 시세·차트는 증권사 자체 데이터 전제라 /api/broker/* 유지.
(function () {
  'use strict';

  const FALLBACK_MESSAGE = 'AI 분석을 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.';
  const NO_DATA_MESSAGE = '이 종목·일자에 대해 제공되는 AI 분석이 아직 없습니다.';
  const UNKNOWN_ETF_MESSAGE = '지원하지 않는 종목입니다. (국내 상장 ETF 대상)';

  // MTS 화면이 쓰는 유일한 진입점 — Publication API 계약(200/204/404/4xx/5xx)을 화면 상태로
  // 해석하는 자리가 여기다(구 mock-broker 중계의 매핑을 위젯으로 이관, ALPHA-992).
  // resolve 값: { state: 'OK', data } | { state: 'NO_DATA', message } | { state: 'FALLBACK', message }
  function getAiAnalysis(ticker, tradeDate) {
    let url = '/api/v1/explanations/' + encodeURIComponent(ticker);
    if (tradeDate) {
      url += '?' + new URLSearchParams({ trade_date: tradeDate }).toString();
    }
    // 타임아웃 필수 — 응답 없는 pending 이 AI 탭을 스켈레톤에 가두지 않게(재진입이 재시도)
    return fetch(url, { signal: AbortSignal.timeout(5000) })
      .then(function (res) {
        if (res.status === 200) {
          return res.json().then(function (data) {
            return { state: 'OK', data: data };
          });
        }
        if (res.status === 204) {
          return { state: 'NO_DATA', message: NO_DATA_MESSAGE };
        }
        if (res.status === 404) {
          return { state: 'NO_DATA', message: UNKNOWN_ETF_MESSAGE };
        }
        if (res.status === 400) {
          // 400은 일시 장애가 아니라 호출측 통합 버그 신호 — 폴백 문구로 코팅하되 로그에 드러낸다
          console.warn('[broker-api] Publication API 400 — 요청 파라미터 확인 필요 (ticker=' + ticker + ', trade_date=' + (tradeDate || '') + ')');
        }
        return { state: 'FALLBACK', message: FALLBACK_MESSAGE };
      })
      .catch(function (err) {
        console.warn('[broker-api] Publication API 호출 실패', err);
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
