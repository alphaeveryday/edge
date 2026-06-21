import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {
  mapAnalysisToWidgetResponse as adapterMapAnalysisToWidgetResponse,
  createFallbackResponse,
  DEFAULT_DISCLAIMER,
  ERROR_MESSAGE,
} from '../src/gateway-adapter.js';
import {
  successAnalysisResponse,
  emptyAnalysisResponse,
} from '../src/fixtures/analysis-response.fixture.js';

const widgetSource = fs.readFileSync(path.resolve(__dirname, '../widget.js'), 'utf8');
const ANALYSIS_V1_SUMMARY =
  '이번 삼성전자 하락은 반도체 규제 뉴스가 가장 크게 작용했어요. 전체 설명 중 절반 이상은 미국의 중국향 반도체 수출 규제 강화로 보는 게 자연스러워요. 삼성전자는 중국 반도체 수요와 장비 규제에 민감하고, 과거 비슷한 규제 뉴스 43건에서도 평균 -1.8% 하락했으며 72%는 같은 하락 방향이었어요. 그다음은 메모리 반도체 수요 전망 하향이에요. 비중은 규제 뉴스의 절반보다 작지만, 삼성전자 이익 기대를 낮추는 보조 악재로 작용했어요. 과거 비슷한 수요 전망 하향 뉴스 31건에서도 평균 -0.9% 하락했어요. 원화 약세는 영향이 있더라도 작게 보는 게 맞아요. 시장 전체나 수출주 전반에는 영향을 줄 수 있지만, 이번 삼성전자 하락을 직접 설명하는 핵심 요인으로 보기엔 근거가 약해요.';
let internals;

function bootstrapInternals() {
  if (!internals) {
    window.__EDGE_WIDGET_TEST_MODE__ = true;
    window.eval(widgetSource);
    internals = window.__EDGE_WIDGET_INTERNALS__;
  }
}

function buildScript(overrides = {}) {
  const script = document.createElement('script');
  const values = {
    embedKey: overrides.embedKey === undefined ? 'pub_demo_1234' : overrides.embedKey,
    clientId: overrides.clientId === undefined ? 'demo-sec' : overrides.clientId,
    widgetId: overrides.widgetId === undefined ? 'asset-event-impact' : overrides.widgetId,
    symbol: overrides.symbol === undefined ? '005930' : overrides.symbol,
    theme: overrides.theme === undefined ? 'default' : overrides.theme,
  };

  if (values.embedKey !== null) script.setAttribute('data-embed-key', values.embedKey);
  if (values.clientId !== null) script.setAttribute('data-client-id', values.clientId);
  if (values.widgetId !== null) script.setAttribute('data-widget-id', values.widgetId);
  if (values.symbol !== null) script.setAttribute('data-symbol', values.symbol);
  if (values.theme !== null) script.setAttribute('data-theme', values.theme);

  if (Object.prototype.hasOwnProperty.call(overrides, 'mockStatus') && overrides.mockStatus !== null) {
    script.setAttribute('data-mock-status', overrides.mockStatus);
  }

  return script;
}

describe('S104 Edge widget helpers', () => {
  beforeAll(() => {
    bootstrapInternals();
  });

  beforeEach(() => {
    document.body.innerHTML = '';
    document.getElementById('edge-widget-style')?.remove();
  });

  it('readConfig가 모든 data attribute를 읽는다', () => {
    const script = buildScript({ mockStatus: 'empty' });
    document.body.appendChild(script);

    const config = internals.readConfig(script);

    expect(config).toEqual({
      embedKey: 'pub_demo_1234',
      clientId: 'demo-sec',
      widgetId: 'asset-event-impact',
      symbol: '005930',
      theme: 'default',
      mockStatus: 'empty',
      gatewayMode: 'mock',
      gatewayUrl: '',
    });
  });

  it('readConfig가 theme이 없을 때 default를 적용한다', () => {
    const config = internals.readConfig(buildScript({ theme: null }));

    expect(config.theme).toBe('default');
  });

  it('readConfig가 알 수 없는 theme을 default로 fallback한다', () => {
    const config = internals.readConfig(buildScript({ theme: 'dark' }));

    expect(config.theme).toBe('default');
  });

  it('readConfig가 symbol을 trim하고 canonical 변환은 하지 않는다', () => {
    const config = internals.readConfig(buildScript({ symbol: '  KRX:005930  ' }));

    expect(config.symbol).toBe('KRX:005930');
  });

  it('validateConfig가 embedKey 누락을 잡는다', () => {
    const validation = internals.validateConfig(internals.readConfig(buildScript({ embedKey: null })));

    expect(validation.ok).toBe(false);
    expect(validation.message).toContain('data-embed-key');
  });

  it('validateConfig가 widgetId 누락을 잡는다', () => {
    const validation = internals.validateConfig(internals.readConfig(buildScript({ widgetId: null })));

    expect(validation.ok).toBe(false);
    expect(validation.message).toContain('data-widget-id');
  });

  it('validateConfig가 symbol 누락을 잡는다', () => {
    const validation = internals.validateConfig(internals.readConfig(buildScript({ symbol: '   ' })));

    expect(validation.ok).toBe(false);
    expect(validation.message).toContain('data-symbol');
  });

  it('validateConfig가 clientId 누락은 허용한다', () => {
    const validation = internals.validateConfig(internals.readConfig(buildScript({ clientId: null })));

    expect(validation.ok).toBe(true);
  });

  it('createGatewayRequest가 S016 request 객체를 만들고 mockStatus를 제외한다', () => {
    const config = internals.readConfig(buildScript({ mockStatus: 'fallback' }));

    const request = internals.createGatewayRequest(config);

    expect(request).toEqual({
      embedKey: 'pub_demo_1234',
      widgetId: 'asset-event-impact',
      symbol: '005930',
      theme: 'default',
      clientId: 'demo-sec',
    });
    expect(request).not.toHaveProperty('mockStatus');
  });

  it('createGatewayRequest가 clientId 누락 시 request에서 생략한다', () => {
    const config = internals.readConfig(buildScript({ clientId: null }));

    const request = internals.createGatewayRequest(config);

    expect(request).toMatchObject({
      embedKey: 'pub_demo_1234',
      widgetId: 'asset-event-impact',
      symbol: '005930',
      theme: 'default',
    });
    expect(request).not.toHaveProperty('clientId');
  });

  it('normalizeStatus가 잘못된 status를 success로 fallback한다', () => {
    expect(internals.normalizeStatus('unknown')).toBe('success');
    expect(internals.readConfig(buildScript({ mockStatus: 'unknown' })).mockStatus).toBe('success');
  });

  it('fetchMockGateway가 success, empty, error, fallback 상태를 반환한다', async () => {
    const request = internals.createGatewayRequest(internals.readConfig(buildScript()));

    const success = await internals.fetchMockGateway(request, 'success');
    const empty = await internals.fetchMockGateway(request, 'empty');
    const error = await internals.fetchMockGateway(request, 'error');
    const fallback = await internals.fetchMockGateway(request, 'fallback');

    expect(success.status).toBe('success');
    expect(success.summary).toBe(ANALYSIS_V1_SUMMARY);
    expect(empty.status).toBe('empty');
    expect(empty.cards).toEqual([]);
    expect(error.status).toBe('error');
    expect(error.message).toBe('위젯 응답 변환 중 문제가 발생했습니다.');
    expect(fallback.status).toBe('fallback');
    expect(fallback.summary).toBe(ANALYSIS_V1_SUMMARY);
    expect(fallback.fallback).toEqual({
      isFallback: true,
      reason: '실시간 분석 데이터를 수집할 수 없습니다.',
      basedAt: '2026-03-12T14:45:00+09:00',
    });
  });

  it('renderSuccess가 symbol, summary, disclaimer를 DOM에 표시한다', async () => {
    const container = document.createElement('div');
    const request = internals.createGatewayRequest(internals.readConfig(buildScript()));
    const response = await internals.fetchMockGateway(request, 'success');

    internals.renderSuccess(container, response);

    expect(container.textContent).toContain('005930');
    expect(container.textContent).toContain(ANALYSIS_V1_SUMMARY);
    expect(container.textContent).toContain('본 정보는 투자 참고용이며, 투자 판단의 최종 책임은 투자자 본인에게 있습니다.');
    expect(container.textContent).toContain('가격 변동 설명');
  });

  it('renderSuccess가 metric 필드를 더 이상 표시하지 않는다', () => {
    const container = document.createElement('div');

    internals.renderSuccess(container, {
      status: 'success',
      symbol: '005930',
      summary: ANALYSIS_V1_SUMMARY,
      cards: [{
        title: '가격 변동 설명',
        description: ANALYSIS_V1_SUMMARY,
        impactDirection: 'negative',
        newsImpactScore: 0.91,
        abnormalReturn: -0.0214,
      }],
      disclaimer: '본 정보는 투자 참고용입니다.',
      newsLinks: [],
    });

    const text = container.textContent;
    expect(text).not.toContain('영향 방향:');
    expect(text).not.toContain('뉴스 임팩트 점수:');
    expect(text).not.toContain('초과 수익률:');
  });

  it('renderSuccess가 card.title이 없으면 가격 변동 설명 fallback label을 표시한다', () => {
    const container = document.createElement('div');

    internals.renderSuccess(container, {
      status: 'success',
      symbol: '005930',
      summary: ANALYSIS_V1_SUMMARY,
      cards: [{ title: null, description: ANALYSIS_V1_SUMMARY }],
      disclaimer: '본 정보는 투자 참고용입니다.',
      newsLinks: [],
    });

    const titleEl = container.querySelector('.edge-widget-card-title');
    expect(titleEl).not.toBeNull();
    expect(titleEl.textContent).toBe('가격 변동 설명');
  });

  it('renderSuccess가 card.title이 있으면 해당 title을 표시한다', () => {
    const container = document.createElement('div');

    internals.renderSuccess(container, {
      status: 'success',
      symbol: '005930',
      summary: ANALYSIS_V1_SUMMARY,
      cards: [{ title: '반도체 규제 이슈 영향', description: ANALYSIS_V1_SUMMARY }],
      disclaimer: '본 정보는 투자 참고용입니다.',
      newsLinks: [],
    });

    const titleEl = container.querySelector('.edge-widget-card-title');
    expect(titleEl.textContent).toBe('반도체 규제 이슈 영향');
  });

  it('initEdgeWidget이 empty, error, fallback 상태를 DOM에 렌더링한다', async () => {
    const emptyScript = buildScript({ mockStatus: 'empty' });
    const errorScript = buildScript({ mockStatus: 'error' });
    const fallbackScript = buildScript({ mockStatus: 'fallback' });
    document.body.append(emptyScript, errorScript, fallbackScript);

    await internals.initEdgeWidget(emptyScript);
    await internals.initEdgeWidget(errorScript);
    await internals.initEdgeWidget(fallbackScript);

    const text = document.body.textContent;
    expect(text).toContain('해당 종목의 최신 분석 결과가 없습니다.');
    expect(text).toContain('위젯 응답 변환 중 문제가 발생했습니다.');
    expect(text).toContain('실시간 분석 데이터를 수집할 수 없습니다.');
    expect(text).toContain('기준 시각: 2026-03-12T14:45:00+09:00');
    expect(text).toContain(ANALYSIS_V1_SUMMARY);
  });

  it('injectStyle은 document.head에 style을 1회만 삽입한다', () => {
    const container = document.createElement('div');

    internals.injectStyle(container);
    internals.injectStyle(container);

    const styles = document.head.querySelectorAll('#edge-widget-style');
    expect(styles).toHaveLength(1);
    expect(styles[0].textContent).toContain('.edge-widget-root');
    expect(container.querySelector('#edge-widget-style')).toBeNull();
  });

  it('escapeHtml이 잠재적인 HTML을 안전하게 이스케이프한다', () => {
    expect(internals.escapeHtml('<script>alert(1)</script>')).toBe('&lt;script&gt;alert(1)&lt;/script&gt;');
  });
});

describe('S015 script loader', () => {
  beforeAll(() => {
    bootstrapInternals();
  });

  beforeEach(() => {
    document.body.innerHTML = '';
    document.getElementById('edge-widget-style')?.remove();
  });

  it('getCurrentScript는 currentScript가 없으면 null을 반환하고, scriptOverride로 초기화한다', async () => {
    expect(internals.getCurrentScript()).toBeNull();

    const script = buildScript();
    document.body.appendChild(script);

    await internals.initEdgeWidget(script);

    const container = script.nextSibling;
    expect(container).not.toBeNull();
    expect(container.className).toBe('edge-widget-root');
  });

  it('createContainer가 script 태그 바로 뒤에 container를 삽입한다', () => {
    const script = buildScript();
    document.body.appendChild(script);

    const container = internals.createContainer(script);

    expect(container.className).toBe('edge-widget-root');
    expect(script.nextSibling).toBe(container);
    expect(container.parentNode).toBe(script.parentNode);
  });

  it('renderLoading이 로딩 UI를 렌더링한다', () => {
    const container = document.createElement('div');

    internals.renderLoading(container);

    expect(container.querySelector('.edge-widget-loading')).not.toBeNull();
    expect(container.textContent).toContain('위젯을 불러오는 중입니다');
  });

  it('initEdgeWidget(scriptOverride)가 container 생성 후 mock 응답 렌더링까지 이어진다', async () => {
    const script = buildScript({ mockStatus: 'success' });
    document.body.appendChild(script);

    await internals.initEdgeWidget(script);

    const container = script.nextSibling;
    expect(container.className).toBe('edge-widget-root');
    expect(container.querySelector('.edge-widget-card')).not.toBeNull();
    expect(container.textContent).toContain('자산 분석 위젯 (005930)');
    expect(container.textContent).toContain(ANALYSIS_V1_SUMMARY);
  });

  it('injectStyle은 document.head에 edge-widget-style을 1회만 삽입한다', () => {
    internals.injectStyle();
    internals.injectStyle();

    const styles = document.head.querySelectorAll('#edge-widget-style');
    expect(styles).toHaveLength(1);
    expect(styles[0].textContent).toContain('.edge-widget-root');
  });

  it('테스트 모드에서만 window.__EDGE_WIDGET_INTERNALS__를 노출한다', () => {
    delete window.__EDGE_WIDGET_INTERNALS__;
    window.__EDGE_WIDGET_TEST_MODE__ = true;

    window.eval(widgetSource);

    expect(window.__EDGE_WIDGET_INTERNALS__).toBeDefined();
    expect(typeof window.__EDGE_WIDGET_INTERNALS__.initEdgeWidget).toBe('function');

    window.__EDGE_WIDGET_INTERNALS__ = internals;
  });

  it('일반 실행 모드에서는 불필요한 전역 객체를 노출하지 않는다', () => {
    delete window.__EDGE_WIDGET_INTERNALS__;
    window.__EDGE_WIDGET_TEST_MODE__ = false;

    window.eval(widgetSource);

    expect(window.__EDGE_WIDGET_INTERNALS__).toBeUndefined();

    // 후속 테스트를 위해 테스트 모드와 내부 노출 상태를 복구한다.
    window.__EDGE_WIDGET_TEST_MODE__ = true;
    window.__EDGE_WIDGET_INTERNALS__ = internals;
  });
});

describe('S049 widget local-api mode', () => {
  beforeAll(() => {
    bootstrapInternals();
  });

  beforeEach(() => {
    document.body.innerHTML = '';
    document.getElementById('edge-widget-style')?.remove();
  });

  it('createGatewayRequest는 gatewayMode/gatewayUrl/mockStatus를 request에서 제외한다', () => {
    const script = buildScript({ mockStatus: 'success' });
    script.setAttribute('data-gateway-mode', 'local-api');
    script.setAttribute('data-gateway-url', '/mock-gateway/widget-analysis');
    const config = internals.readConfig(script);
    expect(config.gatewayMode).toBe('local-api');
    expect(config.gatewayUrl).toBe('/mock-gateway/widget-analysis');

    const request = internals.createGatewayRequest(config);
    expect(request).not.toHaveProperty('gatewayMode');
    expect(request).not.toHaveProperty('gatewayUrl');
    expect(request).not.toHaveProperty('mockStatus');
  });

  it('fetchGateway는 기본 config에서 mock mode를 사용한다', async () => {
    const response = await internals.fetchGateway(
      { symbol: '005930', widgetId: 'asset-event-impact', clientId: 'demo-sec' },
      { gatewayMode: 'mock', mockStatus: 'success' },
    );
    expect(response.status).toBe('success');
    expect(response.summary).toContain('삼성전자');
  });

  it('fetchGateway는 local-api mode에서 gatewayUrl로 POST 요청을 보낸다', async () => {
    const localResponse = {
      status: 'success',
      symbol: '005930',
      summary: 'LOCAL_SUMMARY',
      cards: [{ title: '가격 변동 설명', description: 'LOCAL_SUMMARY' }],
      disclaimer: 'D',
      newsLinks: [],
      fallback: { isFallback: false, reason: null, basedAt: null },
    };
    const fetchSpy = vi.fn(async () => ({ ok: true, json: async () => localResponse }));
    const originalFetch = window.fetch;
    window.fetch = fetchSpy;

    try {
      const request = { symbol: '005930', widgetId: 'asset-event-impact', clientId: 'demo-sec' };
      const response = await internals.fetchGateway(request, {
        gatewayMode: 'local-api',
        gatewayUrl: '/mock-gateway/widget-analysis',
      });

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [url, init] = fetchSpy.mock.calls[0];
      expect(url).toBe('/mock-gateway/widget-analysis');
      expect(init.method).toBe('POST');
      expect(JSON.parse(init.body).symbol).toBe('005930');
      expect(response.summary).toBe('LOCAL_SUMMARY');
    } finally {
      window.fetch = originalFetch;
    }
  });

  it('initEdgeWidget이 local-api mode에서 fetch 응답을 렌더링한다', async () => {
    const localResponse = {
      status: 'success',
      symbol: '005930',
      summary: 'LOCAL_RENDER_SUMMARY',
      cards: [{ title: '가격 변동 설명', description: 'LOCAL_RENDER_SUMMARY' }],
      disclaimer: 'D',
      newsLinks: [],
      fallback: { isFallback: false, reason: null, basedAt: null },
    };
    const originalFetch = window.fetch;
    window.fetch = vi.fn(async () => ({ ok: true, json: async () => localResponse }));

    try {
      const script = buildScript();
      script.setAttribute('data-gateway-mode', 'local-api');
      script.setAttribute('data-gateway-url', '/mock-gateway/widget-analysis');
      document.body.appendChild(script);

      await internals.initEdgeWidget(script);

      const container = script.nextSibling;
      expect(container.textContent).toContain('LOCAL_RENDER_SUMMARY');
    } finally {
      window.fetch = originalFetch;
    }
  });

  it('local-api fetch 실패 시 renderError로 표시한다', async () => {
    const originalFetch = window.fetch;
    window.fetch = vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) }));

    try {
      const script = buildScript();
      script.setAttribute('data-gateway-mode', 'local-api');
      script.setAttribute('data-gateway-url', '/mock-gateway/widget-analysis');
      document.body.appendChild(script);

      await internals.initEdgeWidget(script);

      const container = script.nextSibling;
      expect(container.textContent).toContain('위젯을 불러오는 중 문제가 발생했습니다.');
    } finally {
      window.fetch = originalFetch;
    }
  });
});

describe('option C: mock 스냅샷 ↔ src adapter 일관성', () => {
  beforeAll(() => {
    bootstrapInternals();
  });

  const request = {
    embedKey: 'pub_demo_1234',
    clientId: 'demo-sec',
    widgetId: 'asset-event-impact',
    symbol: '005930',
    theme: 'default',
  };

  it('mock success가 src adapter success 출력과 일치한다', async () => {
    const mock = await internals.fetchMockGateway(request, 'success');
    const canonical = adapterMapAnalysisToWidgetResponse(successAnalysisResponse, request, {
      disclaimer: DEFAULT_DISCLAIMER,
    });
    expect(mock).toEqual(canonical);
  });

  it('mock empty가 src adapter empty 출력과 일치한다', async () => {
    const mock = await internals.fetchMockGateway(request, 'empty');
    const canonical = adapterMapAnalysisToWidgetResponse(emptyAnalysisResponse, request, {
      disclaimer: DEFAULT_DISCLAIMER,
    });
    expect(mock).toEqual(canonical);
  });

  it('mock fallback이 src adapter createFallbackResponse 출력과 일치한다', async () => {
    const mock = await internals.fetchMockGateway(request, 'fallback');
    const success = adapterMapAnalysisToWidgetResponse(successAnalysisResponse, request, {
      disclaimer: DEFAULT_DISCLAIMER,
    });
    const canonical = createFallbackResponse(
      success,
      '실시간 분석 데이터를 수집할 수 없습니다.',
      '2026-03-12T14:45:00+09:00',
    );
    expect(mock).toEqual(canonical);
  });

  it('mock error가 src adapter ERROR_MESSAGE와 일치한다', async () => {
    const mock = await internals.fetchMockGateway(request, 'error');
    expect(mock.status).toBe('error');
    expect(mock.message).toBe(ERROR_MESSAGE);
    expect(mock.symbol).toBe('005930');
  });
});
