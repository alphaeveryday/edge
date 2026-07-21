// 가상 MTS 화면 — 홈·검색·종목상세 3화면 (수령 디자인 KODEX 반도체 AI 분석.dc.html 번역, ALPHA-485).
// AI 분석 탭만 BrokerApi(증권사 자체 API)를 호출하고, 시세·호가·뉴스 등 나머지는
// 증권사 자체 데이터라는 전제로 화면 고정값(목업)을 쓴다.
(function () {
  'use strict';

  var UP = '#b91c1c';
  var DOWN = '#1d4ed8';
  var FLAT = '#71717a';

  // 증권사 자체 종목 마스터(목업) — Publication API와 무관한 증권사 데이터.
  // AI 분석 탭의 실제 상태는 온프렘 시드가 결정한다: 091160·069500=200, 305720=204, 비 ETF=404.
  var STOCKS = [
    { ticker: '091160', name: 'KODEX 반도체', code: '091160 · ETF', price: '132,230', chg: '▼ −9.49%', chgDetail: '▼ −13,870 (−9.49%)', color: DOWN },
    { ticker: '069500', name: 'KODEX 200', code: '069500 · ETF', price: '108,000', chg: '▼ −3.48%', chgDetail: '▼ −3,890 (−3.48%)', color: DOWN },
    { ticker: '305720', name: 'KODEX 2차전지산업', code: '305720 · ETF', price: '89,450', chg: '▲ +1.12%', chgDetail: '▲ +990 (+1.12%)', color: UP },
    { ticker: '005930', name: '삼성전자', code: '005930', price: '255,000', chg: '▼ −4.85%', chgDetail: '▼ −13,000 (−4.85%)', color: DOWN },
    { ticker: '000660', name: 'SK하이닉스', code: '000660', price: '1,921,000', chg: '▼ −8.72%', chgDetail: '▼ −183,500 (−8.72%)', color: DOWN },
    { ticker: '133690', name: 'TIGER 미국나스닥100', code: '133690 · ETF', price: '186,800', chg: '▼ −1.94%', chgDetail: '▼ −3,700 (−1.94%)', color: DOWN },
  ];

  var INDICES = [
    { name: '코스피', value: '6,608.42', chg: '▼ −3.62%', color: DOWN },
    { name: '코스닥', value: '788.15', chg: '▼ −2.41%', color: DOWN },
  ];

  var TABS = ['호가', '차트', '뉴스·공시', 'AI 분석', '종목정보', '커뮤니티', '재무'];
  var AI_TAB = 'AI 분석';

  var ASKS = [
    { price: '132,730', qty: '18,204', bar: '52%' },
    { price: '132,630', qty: '24,911', bar: '71%' },
    { price: '132,530', qty: '31,506', bar: '90%' },
    { price: '132,430', qty: '27,340', bar: '78%' },
    { price: '132,330', qty: '26,480', bar: '75%' },
  ];
  var BIDS = [
    { price: '132,230', qty: '22,915', bar: '80%' },
    { price: '132,130', qty: '19,847', bar: '69%' },
    { price: '132,030', qty: '17,332', bar: '60%' },
    { price: '131,930', qty: '20,644', bar: '72%' },
    { price: '131,830', qty: '15,467', bar: '54%' },
  ];
  var PERIODS = ['1일', '1주', '1개월', '3개월', '1년'];
  var CHART_STATS = [
    { label: '시가', value: '141,500' },
    { label: '고가', value: '142,340' },
    { label: '저가', value: '131,610' },
    { label: '거래량', value: '8,214,502' },
    { label: '거래대금', value: '1조 1,204억' },
    { label: '52주 최고', value: '146,100' },
  ];
  var NEWS = [
    { title: '[공시] KODEX 반도체, 분배금 지급 기준일 안내', source: 'KIND', time: '7월 16일 17:20' },
    { title: '반도체 ETF 일제히 급락…"쏠림 구조가 낙폭 키웠다"', source: '연합인포맥스', time: '7월 16일 16:05' },
    { title: '마이크론 급락 여파, 국내 메모리주 동반 약세', source: '이데일리', time: '7월 16일 09:42' },
    { title: 'AI 투자 효율성 논쟁 재점화…반도체 밸류체인 변동성 확대', source: '한국경제', time: '7월 16일 08:15' },
    { title: '[공시] 삼성전자, 자기주식 처분 결과 공시', source: 'KIND', time: '7월 15일 18:00' },
  ];
  var HOLDINGS = [
    { name: 'SK하이닉스', weight: '32.4%', bar: '100%' },
    { name: '삼성전자', weight: '28.1%', bar: '87%' },
    { name: '한미반도체', weight: '4.2%', bar: '13%' },
    { name: 'DB하이텍', weight: '2.8%', bar: '9%' },
    { name: '리노공업', weight: '2.5%', bar: '8%' },
    { name: '이오테크닉스', weight: '2.1%', bar: '6%' },
  ];
  var POSTS = [
    { user: '반도체장인', time: '32분 전', body: '오늘 낙폭은 지수보다 훨씬 크네요. 삼전닉스 비중이 60% 넘으니 어쩔 수 없는 구조인 듯.', likes: 24, replies: 8 },
    { user: 'ETF만삽니다', time: '1시간 전', body: '레버리지 청산 물량이 쏟아진 게 컸다고 봅니다. 실적 얘기는 아직 없어요.', likes: 17, replies: 5 },
    { user: '존버의민족', time: '2시간 전', body: 'AI 분석 탭 보니 외부 충격 + 수급 증폭이라는데, 다들 어떻게 보시나요?', likes: 11, replies: 12 },
    { user: '나스닥연동', time: '3시간 전', body: '마이크론 -12%면 국내 메모리도 버티기 힘들죠. 내일 미국장 보고 판단.', likes: 9, replies: 3 },
  ];
  var FIN_ROWS = [
    { label: '순자산총액(AUM)', value: '2조 8,450억' },
    { label: '기초지수', value: 'KRX 반도체' },
    { label: '총보수', value: '연 0.09%' },
    { label: 'NAV', value: '132,412.08' },
    { label: '괴리율', value: '−0.14%' },
    { label: '추적오차', value: '0.21%' },
    { label: '상장일', value: '2006-06-27' },
    { label: '분배금 지급', value: '연 4회 (1·4·7·10월)' },
  ];

  var state = {
    stock: null,        // STOCKS 항목(또는 미지 티커의 임시 항목)
    tradeDate: null,    // 딥링크 ?trade_date= 전달값
    activeTab: '차트',
    fav: false,
    alertOn: false,
    period: 2,
    liked: {},
    aiRequestSeq: 0,    // 종목 전환 중 도착한 낡은 응답 무시용
  };

  function el(id) {
    return document.getElementById(id);
  }

  var toastTimer = null;
  function showToast(msg) {
    clearTimeout(toastTimer);
    var t = el('toast');
    t.textContent = msg;
    t.style.display = 'block';
    toastTimer = setTimeout(function () {
      t.style.display = 'none';
    }, 1800);
  }

  function notReady() {
    showToast('데모에서 준비 중인 기능입니다');
  }

  function showScreen(name) {
    ['home', 'search', 'stock'].forEach(function (s) {
      el('screen-' + s).classList.toggle('active', s === name);
    });
  }

  // ── 홈 ──────────────────────────────────────────────────────────

  function renderHome() {
    var grid = el('home-indices');
    grid.textContent = '';
    INDICES.forEach(function (ix) {
      var card = document.createElement('div');
      card.style.cssText = 'border:1px solid #e4e4e7;border-radius:8px;padding:12px';
      card.innerHTML =
        '<div style="font-size:12px;color:#71717a"></div>' +
        '<div class="num" style="font-size:17px;font-weight:700;margin-top:4px"></div>' +
        '<div class="num" style="font-size:12px;font-weight:600;margin-top:2px"></div>';
      card.children[0].textContent = ix.name;
      card.children[1].textContent = ix.value;
      card.children[2].textContent = ix.chg;
      card.children[2].style.color = ix.color;
      grid.appendChild(card);
    });
    renderStockRows(el('home-watchlist'), STOCKS.slice(0, 4));
  }

  // 관심종목·검색 결과 공용 행 — 탭하면 종목 상세로
  function renderStockRows(container, stocks) {
    container.textContent = '';
    stocks.forEach(function (s) {
      var btn = document.createElement('button');
      btn.style.cssText = 'display:flex;align-items:center;justify-content:space-between;width:100%;' +
        'padding:12px 0;border:none;border-bottom:1px solid #f0f0f1;background:none;font-family:inherit;cursor:pointer;text-align:left';
      btn.innerHTML =
        '<div><div style="font-size:14px;font-weight:600;color:#18181b"></div>' +
        '<div class="num" style="font-size:11px;color:#a1a1aa;margin-top:2px"></div></div>' +
        '<div style="text-align:right"><div class="num" style="font-size:14px;font-weight:600;color:#18181b"></div>' +
        '<div class="num" style="font-size:12px;font-weight:600;margin-top:2px"></div></div>';
      var left = btn.children[0];
      var right = btn.children[1];
      left.children[0].textContent = s.name;
      left.children[1].textContent = s.code;
      right.children[0].textContent = s.price;
      right.children[1].textContent = s.chg;
      right.children[1].style.color = s.color;
      btn.addEventListener('click', function () {
        openStock(s, null, '차트');
      });
      container.appendChild(btn);
    });
  }

  // ── 검색 ────────────────────────────────────────────────────────

  function renderSearch() {
    var q = el('search-input').value.trim().toLowerCase();
    var filtered = q
      ? STOCKS.filter(function (s) {
          return s.name.toLowerCase().indexOf(q) !== -1 || s.code.indexOf(q) !== -1;
        })
      : STOCKS;
    el('search-section-label').textContent = q ? '검색 결과' : '인기 검색 종목';
    renderStockRows(el('search-results'), filtered);
    el('search-no-results').style.display = q && filtered.length === 0 ? 'block' : 'none';
  }

  // ── 종목 상세 ───────────────────────────────────────────────────

  function openStock(stock, tradeDate, tab) {
    state.stock = stock;
    state.tradeDate = tradeDate;
    state.fav = false;
    state.alertOn = false;
    state.liked = {};
    el('st-name').textContent = stock.name;
    el('st-code').textContent = stock.code;
    el('st-price').textContent = stock.price;
    el('st-chg').textContent = stock.chgDetail;
    el('st-chg').style.color = stock.color;
    renderFavAlert();
    selectTab(tab || '차트');
    showScreen('stock');
    fetchAiAnalysis();
  }

  function selectTab(name) {
    state.activeTab = name;
    var bar = el('tab-bar');
    bar.textContent = '';
    TABS.forEach(function (label) {
      var btn = document.createElement('button');
      btn.textContent = label;
      var active = label === name;
      btn.style.cssText = 'flex-shrink:0;padding:11px 13px;font-size:13px;border:none;background:none;font-family:inherit;cursor:pointer;' +
        'font-weight:' + (active ? '700' : '400') + ';color:' + (active ? '#3f3a33' : '#71717a') + ';' +
        (active ? 'box-shadow:inset 0 -2.5px 0 #FFBC00' : '');
      btn.addEventListener('click', function () {
        selectTab(label);
      });
      bar.appendChild(btn);
    });
    var panels = { '호가': 'panel-book', '차트': 'panel-chart', '뉴스·공시': 'panel-news', 'AI 분석': 'panel-ai', '종목정보': 'panel-info', '커뮤니티': 'panel-talk', '재무': 'panel-fin' };
    Object.keys(panels).forEach(function (label) {
      el(panels[label]).style.display = label === name ? 'block' : 'none';
    });
  }

  function renderFavAlert() {
    var star = el('fav-btn').querySelector('svg');
    star.setAttribute('fill', state.fav ? '#FFBC00' : 'none');
    star.setAttribute('stroke', state.fav ? '#e0a800' : '#71717a');
    var bell = el('alert-btn').querySelector('svg');
    bell.setAttribute('fill', state.alertOn ? '#FFBC00' : 'none');
    bell.setAttribute('stroke', state.alertOn ? '#e0a800' : '#71717a');
  }

  // ── AI 분석 탭 — 유일한 실데이터 경로 (BrokerApi 경유) ─────────

  function fetchAiAnalysis() {
    var seq = ++state.aiRequestSeq;
    el('ai-loading').style.display = 'flex';
    el('ai-body').style.display = 'none';
    el('ai-empty').style.display = 'none';
    el('ai-disclaimer').style.display = 'none';
    el('ai-date').textContent = '';
    el('ai-date-dot').style.display = 'none';
    el('ai-published').textContent = '';
    window.BrokerApi.getAiAnalysis(state.stock.ticker, state.tradeDate)
      .then(function (result) {
        if (seq === state.aiRequestSeq) {
          showAnalysis(result);
        }
      })
      .catch(function (err) {
        // 렌더링 예외까지 스켈레톤 잔류 없이 안내 문구로 수렴시킨다
        console.warn('[app] 렌더링 실패', err);
        if (seq === state.aiRequestSeq) {
          showAnalysis({ state: 'FALLBACK', message: 'AI 분석을 일시적으로 불러올 수 없습니다. 잠시 후 다시 확인해 주세요.' });
        }
      });
  }

  function showAnalysis(result) {
    el('ai-loading').style.display = 'none';
    if (result.state === 'OK') {
      var data = result.data;
      el('ai-date').textContent = data.trade_date;
      el('ai-date-dot').style.display = 'inline';
      // published_at 은 오프셋 포함 ISO 문자열 — 브라우저 로컬(시연 환경=KST) 시각으로 표기한다
      var published = new Date(data.published_at);
      el('ai-published').textContent = ('0' + published.getHours()).slice(-2) + ':' + ('0' + published.getMinutes()).slice(-2) + ' 게시';
      // summary 는 빈 줄 구분 문단 텍스트 — 블록이 여럿이면 첫 블록을 헤드라인으로 올린다(시드 규칙)
      var blocks = data.summary.split(/\n{2,}/);
      var headline = blocks.length > 1 ? blocks.shift() : null;
      el('ai-headline').textContent = headline || '';
      el('ai-headline').style.display = headline ? 'block' : 'none';
      var body = el('ai-paragraphs');
      body.textContent = '';
      blocks.forEach(function (text) {
        var p = document.createElement('p');
        p.style.cssText = 'font-size:14px;line-height:1.75;margin:0 0 14px;text-wrap:pretty';
        p.textContent = text;
        body.appendChild(p);
      });
      el('ai-body').style.display = 'block';
      el('ai-body').classList.add('fade-up');
      el('ai-disclaimer').textContent = '※ ' + data.disclaimer;
      el('ai-disclaimer').style.display = 'block';
    } else {
      el('ai-empty-text').textContent = result.message;
      el('ai-empty').style.display = 'block';
      el('ai-empty').classList.add('fade-up');
    }
  }

  // ── 종목 상세 목업 패널 (증권사 자체 데이터 전제 — 고정값) ─────

  function renderBook() {
    var wrap = el('book-rows');
    ASKS.forEach(function (row) {
      wrap.appendChild(bookRow(row, true));
    });
    BIDS.forEach(function (row) {
      wrap.appendChild(bookRow(row, false));
    });
  }

  function bookRow(row, isAsk) {
    var div = document.createElement('div');
    div.style.cssText = 'display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #fafafa';
    var barCell = document.createElement('div');
    barCell.style.cssText = 'flex:1;display:flex;justify-content:' + (isAsk ? 'flex-end' : 'flex-start');
    var bar = document.createElement('div');
    bar.style.cssText = 'height:18px;background:' + (isAsk ? '#e8eefb' : '#fbe9e7') + ';border-radius:2px;width:' + row.bar;
    barCell.appendChild(bar);
    var priceCell = document.createElement('div');
    priceCell.className = 'num';
    priceCell.style.cssText = 'width:74px;text-align:center;font-size:13px;font-weight:600;color:' + (isAsk ? '#1d4ed8' : '#b91c1c');
    priceCell.textContent = row.price;
    var qtyCell = document.createElement('div');
    qtyCell.className = 'num';
    qtyCell.style.cssText = 'flex:1;font-size:12px;color:#71717a;text-align:' + (isAsk ? 'left' : 'right');
    qtyCell.textContent = row.qty;
    if (isAsk) {
      div.appendChild(barCell);
      div.appendChild(priceCell);
      div.appendChild(qtyCell);
    } else {
      div.appendChild(qtyCell);
      div.appendChild(priceCell);
      div.appendChild(barCell);
    }
    return div;
  }

  function renderChart() {
    var wrap = el('chart-periods');
    wrap.textContent = '';
    PERIODS.forEach(function (label, i) {
      var btn = document.createElement('button');
      btn.textContent = label;
      var active = i === state.period;
      btn.style.cssText = 'font-size:12px;font-weight:' + (active ? '700' : '500') + ';padding:5px 10px;border-radius:999px;' +
        'border:none;font-family:inherit;cursor:pointer;background:' + (active ? '#3f3a33' : '#f4f4f5') + ';color:' + (active ? '#fff' : '#71717a');
      btn.addEventListener('click', function () {
        state.period = i;
        renderChart();
      });
      wrap.appendChild(btn);
    });
    var stats = el('chart-stats');
    stats.textContent = '';
    CHART_STATS.forEach(function (cs) {
      var card = document.createElement('div');
      card.style.cssText = 'border:1px solid #e4e4e7;border-radius:5px;padding:10px';
      card.innerHTML = '<div style="font-size:10px;color:#a1a1aa;letter-spacing:.04em"></div>' +
        '<div class="num" style="font-size:13px;font-weight:600;margin-top:3px"></div>';
      card.children[0].textContent = cs.label;
      card.children[1].textContent = cs.value;
      stats.appendChild(card);
    });
  }

  function renderNews() {
    var wrap = el('panel-news');
    NEWS.forEach(function (n) {
      var btn = document.createElement('button');
      btn.style.cssText = 'display:block;width:100%;padding:13px 0;border:none;border-bottom:1px solid #f0f0f1;background:none;font-family:inherit;cursor:pointer;text-align:left';
      btn.innerHTML = '<div style="font-size:14px;font-weight:500;line-height:1.45;color:#18181b"></div>' +
        '<div style="font-size:11px;color:#a1a1aa;margin-top:4px"></div>';
      btn.children[0].textContent = n.title;
      btn.children[1].textContent = n.source + ' · ' + n.time;
      btn.addEventListener('click', notReady);
      wrap.appendChild(btn);
    });
  }

  function renderHoldings() {
    var wrap = el('info-holdings');
    HOLDINGS.forEach(function (h) {
      var div = document.createElement('div');
      div.style.cssText = 'display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid #f0f0f1';
      div.innerHTML = '<div style="width:128px;font-size:13px;font-weight:500;flex-shrink:0"></div>' +
        '<div style="flex:1;height:8px;background:#f4f4f5;border-radius:999px;overflow:hidden"><div style="height:100%;background:#FFBC00;border-radius:999px;width:' + h.bar + '"></div></div>' +
        '<div class="num" style="width:48px;text-align:right;font-size:13px;font-weight:600"></div>';
      div.children[0].textContent = h.name;
      div.children[2].textContent = h.weight;
      wrap.appendChild(div);
    });
  }

  function renderTalk() {
    var wrap = el('panel-talk');
    wrap.textContent = '';
    POSTS.forEach(function (po, i) {
      var liked = !!state.liked[i];
      var div = document.createElement('div');
      div.style.cssText = 'padding:13px 0;border-bottom:1px solid #f0f0f1';
      div.innerHTML =
        '<div style="display:flex;align-items:center;gap:6px">' +
        '<span style="font-size:12px;font-weight:600;color:#52525b"></span>' +
        '<span style="font-size:11px;color:#a1a1aa"></span></div>' +
        '<div style="font-size:13px;line-height:1.55;margin-top:5px"></div>' +
        '<div style="display:flex;gap:10px;margin-top:7px"></div>';
      div.children[0].children[0].textContent = po.user;
      div.children[0].children[1].textContent = po.time;
      div.children[1].textContent = po.body;
      var actions = div.children[2];
      var like = document.createElement('button');
      like.textContent = '👍 ' + (po.likes + (liked ? 1 : 0));
      like.style.cssText = 'border:none;border-radius:999px;padding:2px 8px;cursor:pointer;font-family:inherit;font-size:11px;' +
        'background:' + (liked ? '#fff7dd' : 'none') + ';font-weight:' + (liked ? '700' : '400') + ';color:' + (liked ? '#60584C' : '#a1a1aa');
      like.addEventListener('click', function () {
        state.liked[i] = !state.liked[i];
        renderTalk();
      });
      var reply = document.createElement('button');
      reply.textContent = '💬 ' + po.replies;
      reply.style.cssText = 'border:none;background:none;padding:2px 4px;cursor:pointer;font-family:inherit;font-size:11px;color:#a1a1aa';
      reply.addEventListener('click', notReady);
      actions.appendChild(like);
      actions.appendChild(reply);
      wrap.appendChild(div);
    });
  }

  function renderFin() {
    var wrap = el('panel-fin');
    FIN_ROWS.forEach(function (f) {
      var div = document.createElement('div');
      div.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:11px 0;border-bottom:1px solid #f0f0f1';
      div.innerHTML = '<span style="font-size:13px;color:#71717a"></span>' +
        '<span class="num" style="font-size:13px;font-weight:600"></span>';
      div.children[0].textContent = f.label;
      div.children[1].textContent = f.value;
      wrap.appendChild(div);
    });
  }

  // ── 배선 ────────────────────────────────────────────────────────

  document.querySelectorAll('[data-not-ready]').forEach(function (btn) {
    btn.addEventListener('click', notReady);
  });
  el('home-search-btn').addEventListener('click', function () {
    el('search-input').value = '';
    renderSearch();
    showScreen('search');
    el('search-input').focus();
  });
  el('stock-search-btn').addEventListener('click', function () {
    el('search-input').value = '';
    renderSearch();
    showScreen('search');
    el('search-input').focus();
  });
  el('search-back-btn').addEventListener('click', function () {
    showScreen('home');
  });
  el('stock-back-btn').addEventListener('click', function () {
    showScreen('home');
  });
  el('search-input').addEventListener('input', renderSearch);
  el('fav-btn').addEventListener('click', function () {
    state.fav = !state.fav;
    renderFavAlert();
    showToast(state.fav ? '관심종목에 추가되었습니다' : '관심종목에서 삭제되었습니다');
  });
  el('alert-btn').addEventListener('click', function () {
    state.alertOn = !state.alertOn;
    renderFavAlert();
    showToast(state.alertOn ? '가격 알림이 설정되었습니다' : '가격 알림이 해제되었습니다');
  });

  renderHome();
  renderBook();
  renderChart();
  renderNews();
  renderHoldings();
  renderTalk();
  renderFin();

  // 데모 조작 딥링크: ?ticker=·?trade_date= 가 있으면 종목 상세의 AI 분석 탭으로 직행한다.
  // 시나리오 표는 README 참조 (091160·069500=200, 305720=204, 미지 코드=404, 형식 오류=400→폴백).
  var params = new URLSearchParams(location.search);
  var ticker = params.get('ticker');
  var tradeDate = params.get('trade_date');
  if (ticker || tradeDate) {
    ticker = ticker || '069500';
    var found = STOCKS.filter(function (s) {
      return s.ticker === ticker;
    })[0];
    openStock(found || { ticker: ticker, name: '알 수 없는 종목', code: ticker, price: '—', chg: '', chgDetail: '', color: FLAT }, tradeDate, AI_TAB);
  }
})();
