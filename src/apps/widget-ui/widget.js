(() => {
  'use strict';

  const ANALYSIS_V1_SUMMARY =
    '이번 삼성전자 하락은 반도체 규제 뉴스가 가장 크게 작용했어요. 전체 설명 중 절반 이상은 미국의 중국향 반도체 수출 규제 강화로 보는 게 자연스러워요. 삼성전자는 중국 반도체 수요와 장비 규제에 민감하고, 과거 비슷한 규제 뉴스 43건에서도 평균 -1.8% 하락했으며 72%는 같은 하락 방향이었어요. 그다음은 메모리 반도체 수요 전망 하향이에요. 비중은 규제 뉴스의 절반보다 작지만, 삼성전자 이익 기대를 낮추는 보조 악재로 작용했어요. 과거 비슷한 수요 전망 하향 뉴스 31건에서도 평균 -0.9% 하락했어요. 원화 약세는 영향이 있더라도 작게 보는 게 맞아요. 시장 전체나 수출주 전반에는 영향을 줄 수 있지만, 이번 삼성전자 하락을 직접 설명하는 핵심 요인으로 보기엔 근거가 약해요.';

  // 이미 Gateway adapter(src/gateway-adapter.js)를 거쳐 변환이 끝난 widget response 스냅샷.
  // widget.js는 analysis -> widget 변환(adapter)을 직접 하지 않는다.
  // mock 모드는 서버 없이 도는 오프라인 대역으로 이 스냅샷을 그대로 돌려줄 뿐이며,
  // 실제 변환은 Gateway(local-api) 쪽 src/gateway-adapter.js에서 한다.
  // 이 스냅샷이 adapter 출력과 어긋나지 않는지는 테스트로 보장한다.
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

  const EDGE_WIDGET_STYLE_ID = 'edge-widget-style';

  function initEdgeWidget(scriptOverride) {
    const script = scriptOverride || getCurrentScript();

    const container = createContainer(script);

    if (!script) {
      if (!window.__EDGE_WIDGET_TEST_MODE__) {
        renderError(container, {
          message: '위젯 실행에 필요한 script 태그를 찾을 수 없습니다.',
        });
      }
      return;
    }

    const config = readConfig(script);
    renderLoading(container);

    const validation = validateConfig(config);
    if (!validation.ok) {
      console.error('[EDGE Widget] validation error:', validation.message);
      renderError(container, {
        message: validation.message,
      });
      return;
    }

    const request = createGatewayRequest(config);
    console.log('[EDGE Widget] request:', JSON.stringify(request));

    return fetchGateway(request, config)
      .then((response) => {
        renderWidget(container, response);
      })
      .catch((error) => {
        console.error('[EDGE Widget] fetchGateway error:', error);
        renderError(container, {
          message: '위젯을 불러오는 중 문제가 발생했습니다.',
          details: error && error.message,
        });
      });
  }

  function getCurrentScript() {
    return document.currentScript || null;
  }

  function readConfig(script) {
    if (!script) {
      return null;
    }

    return {
      embedKey: normalizeAttribute(script.getAttribute('data-embed-key')),
      clientId: normalizeAttribute(script.getAttribute('data-client-id')),
      widgetId: normalizeAttribute(script.getAttribute('data-widget-id')),
      symbol: normalizeSymbolInput(script.getAttribute('data-symbol')),
      theme: normalizeTheme(script.getAttribute('data-theme')),
      mockStatus: normalizeStatus(script.getAttribute('data-mock-status')),
      gatewayMode: normalizeGatewayMode(script.getAttribute('data-gateway-mode')),
      gatewayUrl: normalizeAttribute(script.getAttribute('data-gateway-url')),
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
      { key: 'embedKey', name: 'data-embed-key' },
      { key: 'widgetId', name: 'data-widget-id' },
      { key: 'symbol', name: 'data-symbol' },
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
    const request = {
      embedKey: normalizeAttribute(config.embedKey),
      widgetId: normalizeAttribute(config.widgetId),
      symbol: normalizeSymbolInput(config.symbol),
      theme: normalizeTheme(config.theme),
    };

    const clientId = normalizeAttribute(config.clientId);
    if (clientId) {
      request.clientId = clientId;
    }

    return request;
  }

  function createContainer(script) {
    const container = document.createElement('div');
    container.className = 'edge-widget-root';

    if (script && script.parentNode) {
      script.parentNode.insertBefore(container, script.nextSibling);
    } else {
      document.body.appendChild(container);
    }

    injectStyle();
    return container;
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

    const header = document.createElement('div');
    header.className = 'edge-widget-heading';
    header.textContent = `자산 분석 위젯 (${response.symbol || '-'})`;
    card.appendChild(header);

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

    if (Array.isArray(response.newsLinks) && response.newsLinks.length > 0) {
      const linksWrap = document.createElement('div');
      linksWrap.className = 'edge-widget-news-links';

      const linksTitle = document.createElement('div');
      linksTitle.textContent = '관련 뉴스';
      linksTitle.className = 'edge-widget-news-title';
      linksWrap.appendChild(linksTitle);

      const linksList = document.createElement('ul');
      linksList.className = 'edge-widget-list';

      response.newsLinks.forEach((linkItem) => {
        const item = document.createElement('li');

        const anchor = document.createElement('a');
        anchor.textContent = linkItem.title || '링크';
        anchor.setAttribute('href', escapeAttribute(linkItem.url || '#'));
        anchor.setAttribute('target', '_blank');
        anchor.setAttribute('rel', 'noopener noreferrer');

        const sourceText = document.createTextNode(` (${linkItem.source || '알 수 없음'})`);
        const linkWrapper = document.createElement('span');
        linkWrapper.appendChild(anchor);
        linkWrapper.appendChild(sourceText);

        item.appendChild(linkWrapper);
        linksList.appendChild(item);
      });

      linksWrap.appendChild(linksList);
      card.appendChild(linksWrap);
    }

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

    if (response && response.details) {
      const detail = document.createElement('div');
      detail.className = 'edge-widget-detail';
      detail.textContent = response.details;
      card.appendChild(detail);
    }

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

  function injectStyle() {
    if (document.getElementById(EDGE_WIDGET_STYLE_ID)) {
      return;
    }

    const style = document.createElement('style');
    style.id = EDGE_WIDGET_STYLE_ID;
    style.textContent = `
      .edge-widget-root {
        margin: 14px 0 0;
        font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", "Segoe UI", sans-serif;
        color: #111827;
        line-height: 1.5;
      }

      .edge-widget-root * {
        box-sizing: border-box;
      }

      .edge-widget-card {
        width: 100%;
        border: 1px solid #dfe7f2;
        border-radius: 18px;
        padding: 16px;
        background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
        box-shadow: 0 12px 28px rgba(34, 94, 200, 0.10);
      }

      .edge-widget-heading {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 9px;
        border-radius: 999px;
        background: #eef4ff;
        color: #225ec8;
        font-size: 12px;
        font-weight: 800;
        margin-bottom: 12px;
      }

      .edge-widget-title {
        font-size: 14px;
        font-weight: 800;
        margin-bottom: 8px;
      }

      .edge-widget-card-title {
        margin: 0 0 8px;
        font-size: 16px;
        line-height: 1.35;
        letter-spacing: -0.02em;
      }

      .edge-widget-summary,
      .edge-widget-description,
      .edge-widget-message,
      .edge-widget-empty,
      .edge-widget-detail {
        margin: 7px 0;
        color: #374151;
        white-space: pre-line;
      }

      .edge-widget-summary {
        font-size: 14px;
        font-weight: 600;
        letter-spacing: -0.03em;
      }

      .edge-widget-disclaimer {
        margin-top: 12px;
        padding-top: 10px;
        border-top: 1px solid #edf1f6;
        color: #6b7280;
        font-size: 12px;
      }

      .edge-widget-news-title {
        margin-top: 12px;
        font-weight: 800;
      }

      .edge-widget-list {
        margin: 6px 0;
        padding-left: 16px;
      }

      .edge-widget-list li {
        margin-bottom: 6px;
      }

      .edge-widget-list a {
        color: #225ec8;
        text-decoration: none;
      }

      .edge-widget-list a:hover {
        text-decoration: underline;
      }

      .edge-widget-error .edge-widget-title,
      .edge-widget-fallback .edge-widget-title {
        color: #d64040;
      }

      .edge-widget-loading {
        color: #6b7280;
        font-size: 13px;
      }
    `;
    document.head.appendChild(style);
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

  function normalizeTheme(raw) {
    const normalized = String(raw || 'default').trim().toLowerCase();
    return normalized === 'default' ? 'default' : 'default';
  }

  function normalizeStatus(raw) {
    const normalized = String(raw || 'success').trim().toLowerCase();
    if (normalized === 'success' || normalized === 'empty' || normalized === 'error' || normalized === 'fallback') {
      return normalized;
    }
    return 'success';
  }

  function normalizeGatewayMode(raw) {
    const normalized = String(raw || 'mock').trim().toLowerCase();
    return normalized === 'local-api' ? 'local-api' : 'mock';
  }

  function fetchGateway(request, config) {
    if (config && config.gatewayMode === 'local-api' && config.gatewayUrl) {
      return fetchLocalGateway(request, config.gatewayUrl);
    }
    return fetchMockGateway(request, config && config.mockStatus);
  }

  function fetchLocalGateway(request, gatewayUrl) {
    return fetch(gatewayUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    }).then((response) => {
      if (!response.ok) {
        throw new Error(`Gateway 응답 오류 (status ${response.status})`);
      }
      return response.json();
    });
  }

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


  function escapeHtml(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;')
      .replace(/'/g, '&#x27;')
      .replace(/`/g, '&#x60;');
  }

  function escapeAttribute(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/\"/g, '&quot;')
      .replace(/'/g, '&#x27;')
      .replace(/`/g, '&#x60;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/=/g, '&#x3D;');
  }

  if (window.__EDGE_WIDGET_TEST_MODE__) {
    window.__EDGE_WIDGET_INTERNALS__ = {
      initEdgeWidget,
      getCurrentScript,
      readConfig,
      validateConfig,
      createGatewayRequest,
      createContainer,
      renderLoading,
      fetchMockGateway,
      fetchGateway,
      fetchLocalGateway,
      renderWidget,
      renderSuccess,
      renderEmpty,
      renderError,
      renderFallback,
      injectStyle,
      escapeHtml,
      escapeAttribute,
      clearContainer,
      normalizeAttribute,
      normalizeSymbolInput,
      normalizeTheme,
      normalizeStatus,
      normalizeGatewayMode,
    };
  } else {
    initEdgeWidget();
  }
})();
