// 가상 MTS 화면 — AI 분석 탭. AI 분석 데이터는 BrokerApi(증권사 자체 API)만 호출한다.
// 시세·종목 정보는 증권사 자체 데이터라는 전제로 화면 고정값을 쓴다.
(function () {
  'use strict';

  // 데모 조작: ?ticker=091160 (다른 설명), ?ticker=114800 (204), ?ticker=999999 (5xx 폴백), 그 외 미지 코드 (404)
  const params = new URLSearchParams(location.search);
  const ticker = params.get('ticker') || '069500';
  const tradeDate = params.get('trade_date') || null;

  // 증권사 자체 종목 마스터(목업) — Serving API와 무관한 증권사 데이터
  const ETF_NAMES = {
    '069500': 'KODEX 200',
    '091160': 'KODEX 반도체',
    '114800': 'KODEX 인버스',
  };

  const TAB_NAMES = ['시세', '차트', '호가', 'AI 분석', '뉴스·공시', '구성종목', '투자자', '재무'];
  const AI_TAB = 'AI 분석';

  const el = function (id) {
    return document.getElementById(id);
  };

  function renderHeader() {
    el('etf-name').textContent = ETF_NAMES[ticker] || '알 수 없는 종목';
    el('etf-ticker').textContent = ticker;
  }

  function renderTabs(activeTab) {
    const bar = el('tab-bar');
    bar.textContent = '';
    TAB_NAMES.forEach(function (name) {
      const btn = document.createElement('button');
      btn.className = 'mts-tab' + (name === activeTab ? ' active' : '');
      btn.textContent = name;
      btn.addEventListener('click', function () {
        renderTabs(name);
        el('panel-ai').style.display = name === AI_TAB ? 'flex' : 'none';
        el('panel-other').style.display = name === AI_TAB ? 'none' : 'flex';
        el('other-tab-name').textContent = name;
      });
      bar.appendChild(btn);
    });
  }

  function showAnalysis(state) {
    el('analysis-loading').style.display = 'none';
    if (state.state === 'OK') {
      const data = state.data;
      const time = data.published_at.slice(11, 16); // "HH:mm" — 게시 시각
      el('analysis-meta').textContent = data.trade_date + ' ' + time + ' 기준';
      el('analysis-text').textContent = data.summary;
      el('analysis-text').style.display = 'block';
      el('analysis-text').classList.add('fade-up');
      el('serving-disclaimer').textContent = '※ ' + data.disclaimer;
      el('serving-disclaimer').style.display = 'block';
    } else {
      el('analysis-empty-text').textContent = state.message;
      el('analysis-empty').style.display = 'block';
      el('analysis-empty').classList.add('fade-up');
    }
  }

  renderHeader();
  renderTabs(AI_TAB);
  window.BrokerApi.getAiAnalysis(ticker, tradeDate)
    .then(showAnalysis)
    .catch(function (err) {
      // 렌더링 예외까지 스켈레톤 잔류 없이 안내 문구로 수렴시킨다
      console.warn('[app] 렌더링 실패', err);
      showAnalysis({ state: 'FALLBACK', message: 'AI 분석을 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.' });
    });
})();
