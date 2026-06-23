(() => {
  'use strict';

  const ANALYSIS_V1_SUMMARY =
    '이번 삼성전자 하락은 반도체 규제 뉴스가 가장 크게 작용했어요. 전체 설명 중 절반 이상은 미국의 중국향 반도체 수출 규제 강화로 보는 게 자연스러워요. 삼성전자는 중국 반도체 수요와 장비 규제에 민감하고, 과거 비슷한 규제 뉴스 43건에서도 평균 -1.8% 하락했으며 72%는 같은 하락 방향이었어요. 그다음은 메모리 반도체 수요 전망 하향이에요. 비중은 규제 뉴스의 절반보다 작지만, 삼성전자 이익 기대를 낮추는 보조 악재로 작용했어요. 과거 비슷한 수요 전망 하향 뉴스 31건에서도 평균 -0.9% 하락했어요. 원화 약세는 영향이 있더라도 작게 보는 게 맞아요. 시장 전체나 수출주 전반에는 영향을 줄 수 있지만, 이번 삼성전자 하락을 직접 설명하는 핵심 요인으로 보기엔 근거가 약해요.';

  // 이미 변환이 끝난 widget response 스냅샷. widget.js는 analysis -> widget 변환을 하지 않고
  // 이 스냅샷을 status별로 렌더링만 한다(widget = 렌더). 서버 없이 도는 오프라인 대역이다.
  // 실제 Gateway 연동(adapter 변환, 분석 API 호출, tenant context)은 후속에서 구현한다.
  const MOCK_GENERATED_AT = '2026-03-12T15:30:00+09:00';
  const MOCK_DISCLAIMER =
    '본 정보는 투자 참고용이며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.';
  const MOCK_EMPTY_DISCLAIMER = '해당 종목의 최신 분석 결과가 없습니다.';
  const MOCK_ERROR_MESSAGE = '위젯 응답 변환 중 문제가 발생했습니다.';
  const MOCK_FALLBACK_REASON = '실시간 분석 데이터를 수집할 수 없습니다.';
  const MOCK_FALLBACK_BASED_AT = '2026-03-12T14:45:00+09:00';

  const MOCK_WIDGET_RESPONSE_SUCCESS = {
    status: 'success',
    symbol: '005930',
    generatedAt: MOCK_GENERATED_AT,
    summary: ANALYSIS_V1_SUMMARY,
    cards: [{ title: null, description: ANALYSIS_V1_SUMMARY }],
    disclaimer: MOCK_DISCLAIMER,
    newsLinks: [],
    fallback: { isFallback: false, reason: null, basedAt: null },
  };

  const MOCK_WIDGET_RESPONSE_EMPTY = {
    status: 'empty',
    symbol: '005930',
    generatedAt: MOCK_GENERATED_AT,
    summary: '',
    cards: [],
    disclaimer: MOCK_EMPTY_DISCLAIMER,
    newsLinks: [],
    fallback: { isFallback: false, reason: null, basedAt: null },
  };

  const MOCK_WIDGET_RESPONSE_ERROR = {
    status: 'error',
    symbol: '005930',
    message: MOCK_ERROR_MESSAGE,
  };

  const HEIGHT_MESSAGE_TYPE = 'edge-widget:height';

  // widget.js는 iframe 본체에서 돈다. 입력은 더 이상 script data-*가 아니라 iframe URL의
  // fragment #key=&symbol=이다(쿼리스트링 대신 fragment를 써서 key·symbol이 서버/CDN 로그·Referer에
  // 남지 않게 한다). 로더(widget-loader.js)가 고객사 페이지에서 이 URL을 만든다.
  function initEdgeWidget(hashOverride) {
    const container = createContainer();
    observeHeight(container);

    const config = readConfig(hashOverride);
    renderLoading(container);
    notifyHeight();

    const validation = validateConfig(config);
    if (!validation.ok) {
      console.error('[EDGE Widget] validation error:', validation.message);
      renderError(container, {
        message: validation.message,
      });
      notifyHeight();
      return;
    }

    const request = createGatewayRequest(config);

    return fetchMockGateway(request)
      .then((response) => {
        renderWidget(container, response);
        notifyHeight();
      })
      .catch((error) => {
        console.error('[EDGE Widget] mock gateway error:', error);
        renderError(container, {
          message: '위젯을 불러오는 중 문제가 발생했습니다.',
        });
        notifyHeight();
      });
  }

  function readConfig(hashOverride) {
    const raw = typeof hashOverride === 'string'
      ? hashOverride
      : (typeof window !== 'undefined' ? window.location.hash : '');
    // location.hash는 선행 '#'을 포함한다. 테스트가 '?…'를 넘겨도 동작하도록 '#'·'?' 모두 제거.
    const params = new URLSearchParams(raw.replace(/^[#?]/, ''));

    return {
      embedKey: normalizeAttribute(params.get('key')),
      symbol: normalizeSymbolInput(params.get('symbol')),
    };
  }

  function validateConfig(config) {
    if (!config) {
      return {
        ok: false,
        message: '필수 설정값이 없습니다.',
      };
    }

    const required = [
      { key: 'embedKey', name: 'key' },
      { key: 'symbol', name: 'symbol' },
    ];

    const missing = required
      .filter((item) => !normalizeAttribute(config[item.key]))
      .map((item) => item.name);

    if (missing.length > 0) {
      return {
        ok: false,
        message: `필수 설정값 누락: ${missing.join(', ')}`,
      };
    }

    return {
      ok: true,
      message: '',
    };
  }

  function createGatewayRequest(config) {
    return {
      embedKey: normalizeAttribute(config.embedKey),
      symbol: normalizeSymbolInput(config.symbol),
    };
  }

  function createContainer() {
    const container = document.createElement('div');
    container.className = 'edge-widget-root';
    document.body.appendChild(container);
    return container;
  }

  // 본체 높이를 부모(loader)에 알린다. 높이 값은 민감정보가 아니라 targetOrigin '*'로 보낸다.
  // 수신측의 origin 엄격검증(allowed_origins/frame-ancestors)은 서버 트랙에서 도입한다.
  function notifyHeight() {
    if (typeof window === 'undefined' || window.parent === window) {
      return; // iframe에 임베드된 경우에만 동기화한다.
    }
    window.parent.postMessage(
      { type: HEIGHT_MESSAGE_TYPE, height: measureHeight() },
      '*',
    );
  }

  function measureHeight() {
    if (typeof document === 'undefined' || !document.body) {
      return 0;
    }
    return Math.ceil(document.body.getBoundingClientRect().height);
  }

  // 내용 변화(로딩→렌더, 상태 전환 등)마다 높이를 다시 알린다.
  function observeHeight(target) {
    if (typeof ResizeObserver === 'undefined' || !target) {
      return null;
    }
    const observer = new ResizeObserver(() => notifyHeight());
    observer.observe(target);
    return observer;
  }

  function renderLoading(container) {
    clearContainer(container);

    const title = document.createElement('div');
    title.className = 'edge-widget-title';
    title.textContent = 'EDGE 위젯';

    const status = document.createElement('div');
    status.className = 'edge-widget-loading';
    status.textContent = '위젯을 불러오는 중입니다...';

    const card = document.createElement('div');
    card.className = 'edge-widget-card';
    card.appendChild(title);
    card.appendChild(status);

    container.appendChild(card);
  }

  function renderWidget(container, response) {
    const status = (response && response.status) || 'success';

    if (status === 'success') {
      renderSuccess(container, response);
      return;
    }

    if (status === 'empty') {
      renderEmpty(container, response);
      return;
    }

    if (status === 'fallback') {
      renderFallback(container, response);
      return;
    }

    renderError(container, response);
  }

  function renderSuccess(container, response) {
    clearContainer(container);

    const card = document.createElement('div');
    card.className = 'edge-widget-card';

    const cards = Array.isArray(response.cards) ? response.cards : [];
    const mainCard = cards.length > 0 ? cards[0] : null;

    if (mainCard) {
      const issueTitle = document.createElement('h3');
      issueTitle.className = 'edge-widget-card-title';
      issueTitle.textContent = mainCard.title || '가격 변동 설명';
      card.appendChild(issueTitle);
    }

    const summary = document.createElement('p');
    summary.className = 'edge-widget-summary';
    summary.textContent = response.summary || '';
    card.appendChild(summary);

    if (mainCard) {
      const description = mainCard.description || '';
      if (description && description !== response.summary) {
        const issueDescription = document.createElement('p');
        issueDescription.className = 'edge-widget-description';
        issueDescription.textContent = description;
        card.appendChild(issueDescription);
      }
    }

    const disclaimer = document.createElement('div');
    disclaimer.className = 'edge-widget-disclaimer';
    disclaimer.textContent = response.disclaimer || '';
    card.appendChild(disclaimer);

    // newsLinks 렌더는 이 스파이크 범위가 아니다(v1 응답은 항상 []). 외부 URL을 href에
    // 넣으려면 javascript: 등 위험 scheme을 막는 http/https allowlist가 선행돼야 하므로,
    // 실제 newsLinks 도입(후속) 때 allowlist + 테스트와 함께 추가한다.
    container.appendChild(card);
  }

  function renderEmpty(container, response) {
    clearContainer(container);

    const card = document.createElement('div');
    card.className = 'edge-widget-card';

    const title = document.createElement('div');
    title.className = 'edge-widget-title';
    title.textContent = `${response.symbol || ''} 분석 결과 없음`;

    const message = document.createElement('div');
    message.className = 'edge-widget-empty';
    message.textContent = '해당 종목의 최신 분석 결과가 없습니다.';

    card.appendChild(title);
    card.appendChild(message);
    container.appendChild(card);
  }

  function renderError(container, response) {
    clearContainer(container);

    const card = document.createElement('div');
    card.className = 'edge-widget-card edge-widget-error';

    const title = document.createElement('div');
    title.className = 'edge-widget-title';
    title.textContent = '오류';

    const message = document.createElement('div');
    message.className = 'edge-widget-message';
    message.textContent = response && response.message
      ? response.message
      : '위젯을 불러오는 중 문제가 발생했습니다.';

    card.appendChild(title);
    card.appendChild(message);

    // 내부 오류 상세(details)는 사용자 UI에 노출하지 않는다(정보 유출 방지).
    container.appendChild(card);
  }

  function renderFallback(container, response) {
    clearContainer(container);

    const fallback = response && response.fallback ? response.fallback : {};
    const card = document.createElement('div');
    card.className = 'edge-widget-card edge-widget-fallback';

    const title = document.createElement('div');
    title.className = 'edge-widget-title';
    title.textContent = `${response && response.symbol ? response.symbol : ''} 분석 결과 없음`;

    const status = document.createElement('div');
    status.className = 'edge-widget-empty';
    status.textContent = fallback.reason
      ? fallback.reason
      : '일시적인 데이터 부재로 분석 결과를 표시할 수 없습니다.';

    const basedAt = document.createElement('div');
    basedAt.className = 'edge-widget-detail';
    basedAt.textContent = fallback.basedAt
      ? `기준 시각: ${fallback.basedAt}`
      : '기준 시각 정보가 없습니다.';

    card.appendChild(title);
    card.appendChild(status);
    card.appendChild(basedAt);

    const cards = Array.isArray(response && response.cards) ? response.cards : [];
    const mainCard = cards.length > 0 ? cards[0] : null;
    if (mainCard) {
      const issueTitle = document.createElement('h3');
      issueTitle.className = 'edge-widget-card-title';
      issueTitle.textContent = mainCard.title || '가격 변동 설명';
      card.appendChild(issueTitle);
    }

    const summary = document.createElement('p');
    summary.className = 'edge-widget-summary';
    summary.textContent = response && response.summary ? response.summary : '';
    card.appendChild(summary);

    if (response && response.disclaimer) {
      const disclaimer = document.createElement('div');
      disclaimer.className = 'edge-widget-disclaimer';
      disclaimer.textContent = response.disclaimer;
      card.appendChild(disclaimer);
    }

    container.appendChild(card);
  }

  function clearContainer(container) {
    container.innerHTML = '';
  }

  function normalizeAttribute(value) {
    return String(value || '').trim();
  }

  function normalizeSymbolInput(value) {
    return normalizeAttribute(value);
  }

  function normalizeStatus(raw) {
    const normalized = String(raw || 'success').trim().toLowerCase();
    if (normalized === 'success' || normalized === 'empty' || normalized === 'error' || normalized === 'fallback') {
      return normalized;
    }
    return 'success';
  }

  // 라이브 부팅 경로(initEdgeWidget)는 인자 없이 호출 → 항상 success를 반환한다. mockStatus 인자는
  // empty/error/fallback 렌더를 렌더 단위 테스트에서 검증하기 위한 용도이며, 실제 부팅 경로에선 도달하지 않는다.
  // (실제 Gateway 연동 시 이 함수가 실제 fetch로 교체된다.)
  function fetchMockGateway(request, mockStatus) {
    const status = normalizeStatus(mockStatus);
    const symbol = request && request.symbol ? request.symbol : '';

    return new Promise((resolve) => {
      setTimeout(() => {
        resolve(buildMockWidgetResponse(status, symbol));
      }, 0);
    });
  }

  // mock 모드는 변환을 하지 않는다. 이미 변환이 끝난 widget response 스냅샷을 status별로 돌려준다.
  function buildMockWidgetResponse(status, symbol) {
    if (status === 'error') {
      return { ...MOCK_WIDGET_RESPONSE_ERROR, symbol };
    }
    if (status === 'empty') {
      return { ...MOCK_WIDGET_RESPONSE_EMPTY, symbol };
    }
    if (status === 'fallback') {
      return {
        ...MOCK_WIDGET_RESPONSE_SUCCESS,
        symbol,
        status: 'fallback',
        fallback: {
          isFallback: true,
          reason: MOCK_FALLBACK_REASON,
          basedAt: MOCK_FALLBACK_BASED_AT,
        },
      };
    }
    return { ...MOCK_WIDGET_RESPONSE_SUCCESS, symbol };
  }

  if (window.__EDGE_WIDGET_TEST_MODE__) {
    window.__EDGE_WIDGET_INTERNALS__ = {
      initEdgeWidget,
      readConfig,
      validateConfig,
      createGatewayRequest,
      createContainer,
      notifyHeight,
      measureHeight,
      observeHeight,
      HEIGHT_MESSAGE_TYPE,
      renderLoading,
      fetchMockGateway,
      renderWidget,
      renderSuccess,
      renderEmpty,
      renderError,
      renderFallback,
      clearContainer,
      normalizeAttribute,
      normalizeSymbolInput,
      normalizeStatus,
    };
  } else {
    initEdgeWidget();
  }
})();
