import { describe, expect, it, vi } from 'vitest';
import { handleWidgetAnalysisRequest } from '../src/mock-gateway-service.js';
import {
  ANALYSIS_V1_SUMMARY,
  analysisResponseWithTitleFixture,
} from '../src/fixtures/analysis-response.fixture.js';

const baseBody = {
  embedKey: 'pub_demo_1234',
  clientId: 'demo-sec',
  widgetId: 'asset-event-impact',
  symbol: '005930',
  theme: 'default',
};

describe('S046 mock gateway service', () => {
  it('request body의 symbol로 analysisApiClient를 호출해 success widget response를 반환한다', async () => {
    const response = await handleWidgetAnalysisRequest(baseBody);
    expect(response.status).toBe('success');
    expect(response.symbol).toBe('005930');
    expect(response.summary).toBe(ANALYSIS_V1_SUMMARY);
    expect(response.cards[0].description).toBe(ANALYSIS_V1_SUMMARY);
  });

  it('알 수 없는 symbol이면 empty widget response를 반환한다', async () => {
    const response = await handleWidgetAnalysisRequest({ ...baseBody, symbol: '999999' });
    expect(response.status).toBe('empty');
    expect(response.cards).toEqual([]);
  });

  it('analysisApiClient가 예외를 던지면 error widget response를 반환한다', async () => {
    const response = await handleWidgetAnalysisRequest({ ...baseBody, symbol: 'THROW_ERROR' });
    expect(response.status).toBe('error');
    expect(response.message).toBe('위젯 응답 변환 중 문제가 발생했습니다.');
  });

  it('request body에 symbol이 없으면 error widget response를 반환한다', async () => {
    const response = await handleWidgetAnalysisRequest({ ...baseBody, symbol: '' });
    expect(response.status).toBe('error');
  });

  it('symbol과 mock tenantContext가 analysisApiClient 호출에 전달된다', async () => {
    const spy = vi.fn(async () => ({
      request_id: 'req_spy',
      as_of: '2026-03-12T15:30:00+09:00',
      affected_assets: [],
    }));

    await handleWidgetAnalysisRequest(baseBody, { getLatestAnalysis: spy });

    expect(spy).toHaveBeenCalledTimes(1);
    const arg = spy.mock.calls[0][0];
    expect(arg.symbol).toBe('005930');
    expect(arg.tenantContext).toMatchObject({
      organizationId: 'org_demo_sec',
      applicationId: 'app_mts',
      widgetId: 'asset-event-impact',
      embedKey: 'pub_demo_1234',
      clientId: 'demo-sec',
    });
  });

  it('S049 title optional/pass-through 정책이 유지된다', async () => {
    // 기본 fixture(title 없음) -> cards[0].title null
    const noTitle = await handleWidgetAnalysisRequest(baseBody);
    expect(noTitle.cards[0].title).toBeNull();

    // title을 제공하는 analysis 응답 -> pass-through 매핑
    const withTitleClient = vi.fn(async () => analysisResponseWithTitleFixture);
    const withTitle = await handleWidgetAnalysisRequest(baseBody, {
      getLatestAnalysis: withTitleClient,
    });
    expect(withTitle.cards[0].title).toBe('반도체 규제 이슈 영향');
  });
});
