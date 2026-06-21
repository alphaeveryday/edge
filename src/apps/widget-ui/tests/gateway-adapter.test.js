import { describe, expect, it } from 'vitest';
import {
  DEFAULT_DISCLAIMER,
  EMPTY_DISCLAIMER,
  ERROR_MESSAGE,
  createFallbackResponse,
  findMatchingAsset,
  mapAnalysisToWidgetResponse,
  normalizeSymbolForMatch,
} from '../src/gateway-adapter.js';
import {
  ANALYSIS_V1_SUMMARY,
  analysisResponseWithTitleFixture,
  emptyAnalysisResponse,
  emptySummaryAnalysisResponse,
  otherSymbolAnalysisResponse,
  successAnalysisResponse,
} from '../src/fixtures/analysis-response.fixture.js';

const baseRequest = {
  embedKey: 'pub_demo_1234',
  clientId: 'demo-sec',
  widgetId: 'asset-event-impact',
  symbol: '005930',
  theme: 'default',
};

describe('S049 gateway adapter', () => {
  it('normalizeSymbolForMatch가 plain symbol을 그대로 반환한다', () => {
    expect(normalizeSymbolForMatch('005930')).toBe('005930');
  });

  it('normalizeSymbolForMatch가 .KS suffix를 제거한다', () => {
    expect(normalizeSymbolForMatch('005930.KS')).toBe('005930');
  });

  it('normalizeSymbolForMatch가 KRX: prefix를 제거한다', () => {
    expect(normalizeSymbolForMatch('KRX:005930')).toBe('005930');
  });

  it('findMatchingAsset가 005930 요청으로 005930.KS asset을 찾는다', () => {
    const asset = findMatchingAsset(successAnalysisResponse.affected_assets, '005930');
    expect(asset).not.toBeNull();
    expect(asset.code).toBe('005930.KS');
  });

  it('mapAnalysisToWidgetResponse가 success response를 만든다', () => {
    const response = mapAnalysisToWidgetResponse(successAnalysisResponse, baseRequest);
    expect(response.status).toBe('success');
    expect(response.symbol).toBe('005930');
    expect(response.generatedAt).toBe('2026-03-12T15:30:00+09:00');
    expect(response.cards).toHaveLength(1);
    expect(response.cards[0].title).toBeNull();
  });

  it('success response의 summary와 cards[0].description이 analysis summary와 일치한다', () => {
    const response = mapAnalysisToWidgetResponse(successAnalysisResponse, baseRequest);
    expect(response.summary).toBe(ANALYSIS_V1_SUMMARY);
    expect(response.cards[0].description).toBe(ANALYSIS_V1_SUMMARY);
  });

  it('affected_assets가 비어 있으면 empty response를 반환한다', () => {
    const response = mapAnalysisToWidgetResponse(emptyAnalysisResponse, baseRequest);
    expect(response.status).toBe('empty');
    expect(response.summary).toBe('');
    expect(response.cards).toEqual([]);
    expect(response.disclaimer).toBe(EMPTY_DISCLAIMER);
  });

  it('요청 symbol과 매칭되는 asset이 없으면 empty response를 반환한다', () => {
    const response = mapAnalysisToWidgetResponse(otherSymbolAnalysisResponse, baseRequest);
    expect(response.status).toBe('empty');
    expect(response.symbol).toBe('005930');
  });

  it('summary가 비어 있으면 empty response를 반환한다', () => {
    const response = mapAnalysisToWidgetResponse(emptySummaryAnalysisResponse, baseRequest);
    expect(response.status).toBe('empty');
  });

  it('analysis response가 null이면 error response를 반환한다', () => {
    const response = mapAnalysisToWidgetResponse(null, baseRequest);
    expect(response.status).toBe('error');
    expect(response.message).toBe(ERROR_MESSAGE);
    expect(response.symbol).toBe('005930');
  });

  it('analysis response shape가 잘못되면 error response를 반환한다', () => {
    const response = mapAnalysisToWidgetResponse({ foo: 'bar' }, baseRequest);
    expect(response.status).toBe('error');
  });

  it('disclaimer는 adapter에서 주입되며 options로 override할 수 있다', () => {
    const withDefault = mapAnalysisToWidgetResponse(successAnalysisResponse, baseRequest);
    expect(withDefault.disclaimer).toBe(DEFAULT_DISCLAIMER);

    const overridden = mapAnalysisToWidgetResponse(successAnalysisResponse, baseRequest, {
      disclaimer: '커스텀 디스클레이머',
    });
    expect(overridden.disclaimer).toBe('커스텀 디스클레이머');
  });

  it('newsLinks는 v1에서 빈 배열이다', () => {
    const response = mapAnalysisToWidgetResponse(successAnalysisResponse, baseRequest);
    expect(response.newsLinks).toEqual([]);
  });

  it('impactDirection, newsImpactScore, abnormalReturn은 v1 response에 포함되지 않는다', () => {
    const response = mapAnalysisToWidgetResponse(successAnalysisResponse, baseRequest);
    expect(response).not.toHaveProperty('impactDirection');
    expect(response).not.toHaveProperty('newsImpactScore');
    expect(response).not.toHaveProperty('abnormalReturn');
  });

  it('createFallbackResponse가 success summary를 유지하면서 fallback 상태로 감싼다', () => {
    const success = mapAnalysisToWidgetResponse(successAnalysisResponse, baseRequest);
    const fallback = createFallbackResponse(success, '실시간 분석 데이터를 수집할 수 없습니다.', '2026-03-12T14:45:00+09:00');
    expect(fallback.status).toBe('fallback');
    expect(fallback.fallback.isFallback).toBe(true);
    expect(fallback.fallback.basedAt).toBe('2026-03-12T14:45:00+09:00');
    expect(fallback.summary).toBe(ANALYSIS_V1_SUMMARY);
  });

  it('analysis v1 응답에 title이 없으면 adapter는 title을 생성하지 않는다', () => {
    const response = mapAnalysisToWidgetResponse(successAnalysisResponse, baseRequest);
    expect(response.cards[0].title).toBeNull();
    expect(JSON.stringify(response)).not.toContain('가격 변동 설명');
  });

  it('analysis 응답에 title이 있으면 adapter가 그대로 cards[0].title로 매핑한다', () => {
    const response = mapAnalysisToWidgetResponse(analysisResponseWithTitleFixture, baseRequest);
    expect(response.cards[0].title).toBe('반도체 규제 이슈 영향');
    expect(response.cards[0].description).toBe(response.summary);
  });

  it('adapter는 summary에서 title을 추론하지 않는다 (title이 없으면 항상 null)', () => {
    const custom = {
      request_id: 'req_custom',
      as_of: '2026-03-12T15:30:00+09:00',
      affected_assets: [
        { code: '005930.KS', summary: '반도체 규제가 핵심 이슈입니다. 제목처럼 보이는 첫 문장.' },
      ],
    };
    const response = mapAnalysisToWidgetResponse(custom, baseRequest);
    expect(response.cards[0].title).toBeNull();
    expect(response.cards[0].description).toBe('반도체 규제가 핵심 이슈입니다. 제목처럼 보이는 첫 문장.');
  });
});
