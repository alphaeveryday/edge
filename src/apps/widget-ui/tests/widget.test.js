import { beforeAll, beforeEach, describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

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

// 입력은 iframe URL의 fragment #key=&symbol=다(로더가 생성). data attribute가 아니다.
function frag(overrides = {}) {
  const params = new URLSearchParams();
  const key = overrides.key === undefined ? 'pub_demo_1234' : overrides.key;
  const symbol = overrides.symbol === undefined ? '005930' : overrides.symbol;
  if (key !== null) params.set('key', key);
  if (symbol !== null) params.set('symbol', symbol);
  return '#' + params.toString();
}

describe('Edge widget helpers', () => {
  beforeAll(() => {
    bootstrapInternals();
  });

  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('readConfig가 URL 파라미터(key, symbol)를 읽는다', () => {
    const config = internals.readConfig(frag());

    expect(config).toEqual({
      embedKey: 'pub_demo_1234',
      symbol: '005930',
    });
  });

  it('readConfig가 mock 등 계약 외 파라미터는 무시한다', () => {
    const config = internals.readConfig('#key=pub_demo_1234&symbol=005930&mock=error');

    expect(config).toEqual({
      embedKey: 'pub_demo_1234',
      symbol: '005930',
    });
    expect(config).not.toHaveProperty('mockStatus');
  });

  it('readConfig가 symbol을 trim하고 canonical 변환은 하지 않는다', () => {
    const config = internals.readConfig(frag({ symbol: '  KRX:005930  ' }));

    expect(config.symbol).toBe('KRX:005930');
  });

  it('validateConfig가 key 누락을 잡는다', () => {
    const validation = internals.validateConfig(internals.readConfig(frag({ key: null })));

    expect(validation.ok).toBe(false);
    expect(validation.message).toContain('key');
  });

  it('validateConfig가 key+symbol만으로 통과한다 (widgetId/theme/clientId는 계약에서 제외)', () => {
    const validation = internals.validateConfig(internals.readConfig(frag()));

    expect(validation.ok).toBe(true);
  });

  it('validateConfig가 symbol 누락을 잡는다', () => {
    const validation = internals.validateConfig(internals.readConfig(frag({ symbol: '   ' })));

    expect(validation.ok).toBe(false);
    expect(validation.message).toContain('symbol');
  });

  it('createGatewayRequest가 key+symbol request를 만든다 (그 외 필드 제외)', () => {
    const request = internals.createGatewayRequest(internals.readConfig(frag()));

    expect(request).toEqual({
      embedKey: 'pub_demo_1234',
      symbol: '005930',
    });
    expect(request).not.toHaveProperty('mockStatus');
    expect(request).not.toHaveProperty('widgetId');
    expect(request).not.toHaveProperty('theme');
    expect(request).not.toHaveProperty('clientId');
  });

  it('normalizeStatus가 유효 status는 통과시키고 그 외만 success로 막는다', () => {
    // 유효 status는 그대로 통과해야 테스트가 해당 렌더 경로(empty/error/fallback)를 선택할 수 있다.
    // passthrough가 깨지면 mock 기반 렌더 테스트가 엉뚱한 상태를 그리게 되므로 여기서 잡는다.
    expect(internals.normalizeStatus('empty')).toBe('empty');
    expect(internals.normalizeStatus('error')).toBe('error');
    expect(internals.normalizeStatus('fallback')).toBe('fallback');
    // 알 수 없는/누락 status만 success로 막는다.
    expect(internals.normalizeStatus('unknown')).toBe('success');
    expect(internals.normalizeStatus(undefined)).toBe('success');
  });

  it('fetchMockGateway가 success, empty, error, fallback 상태를 반환한다', async () => {
    const request = internals.createGatewayRequest(internals.readConfig(frag()));

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

  it('fetchMockGateway는 status 미지정 시 success를 반환한다', async () => {
    const request = internals.createGatewayRequest(internals.readConfig(frag()));

    const response = await internals.fetchMockGateway(request);

    expect(response.status).toBe('success');
  });

  it('renderSuccess가 summary, disclaimer, 카드 제목을 DOM에 표시한다', async () => {
    const container = document.createElement('div');
    const request = internals.createGatewayRequest(internals.readConfig(frag()));
    const response = await internals.fetchMockGateway(request, 'success');

    internals.renderSuccess(container, response);

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

  // mock-status(PoC)를 제거했으므로 empty/error/fallback은 라이브 경로 대신 renderWidget으로 직접 검증한다.
  it('renderWidget이 empty, error, fallback 상태를 DOM에 렌더링한다', async () => {
    const request = internals.createGatewayRequest(internals.readConfig(frag()));
    const empty = await internals.fetchMockGateway(request, 'empty');
    const error = await internals.fetchMockGateway(request, 'error');
    const fallback = await internals.fetchMockGateway(request, 'fallback');

    const emptyContainer = document.createElement('div');
    const errorContainer = document.createElement('div');
    const fallbackContainer = document.createElement('div');

    internals.renderWidget(emptyContainer, empty);
    internals.renderWidget(errorContainer, error);
    internals.renderWidget(fallbackContainer, fallback);

    expect(emptyContainer.textContent).toContain('해당 종목의 최신 분석 결과가 없습니다.');
    expect(errorContainer.textContent).toContain('위젯 응답 변환 중 문제가 발생했습니다.');
    expect(fallbackContainer.textContent).toContain('실시간 분석 데이터를 수집할 수 없습니다.');
    expect(fallbackContainer.textContent).toContain('기준 시각: 2026-03-12T14:45:00+09:00');
    expect(fallbackContainer.textContent).toContain(ANALYSIS_V1_SUMMARY);
  });
});

describe('widget bootstrap (iframe 본체)', () => {
  beforeAll(() => {
    bootstrapInternals();
  });

  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('createContainer가 document.body에 root container를 추가한다', () => {
    const container = internals.createContainer();

    expect(container.className).toBe('edge-widget-root');
    expect(container.parentNode).toBe(document.body);
  });

  it('renderLoading이 로딩 UI를 렌더링한다', () => {
    const container = document.createElement('div');

    internals.renderLoading(container);

    expect(container.querySelector('.edge-widget-loading')).not.toBeNull();
    expect(container.textContent).toContain('위젯을 불러오는 중입니다');
  });

  it('initEdgeWidget(search)가 URL 입력으로 부팅해 success를 렌더링한다', async () => {
    await internals.initEdgeWidget(frag());

    const container = document.querySelector('.edge-widget-root');
    expect(container).not.toBeNull();
    expect(container.querySelector('.edge-widget-card')).not.toBeNull();
    expect(container.textContent).toContain('가격 변동 설명');
    expect(container.textContent).toContain(ANALYSIS_V1_SUMMARY);
  });

  it('initEdgeWidget(search)가 key 누락 시 검증 에러를 렌더링한다', async () => {
    await internals.initEdgeWidget(frag({ key: null }));

    const container = document.querySelector('.edge-widget-root');
    expect(container.textContent).toContain('필수 설정값 누락');
    expect(container.textContent).toContain('key');
  });

  it('measureHeight는 document.body 높이를 px 정수로 반환한다', () => {
    expect(typeof internals.measureHeight()).toBe('number');
    expect(Number.isInteger(internals.measureHeight())).toBe(true);
  });

  it('notifyHeight는 iframe에 임베드되지 않으면(window.parent === window) 아무 일도 하지 않는다', () => {
    // jsdom에서 window.parent === window이므로 throw 없이 조용히 반환해야 한다.
    expect(() => internals.notifyHeight()).not.toThrow();
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
