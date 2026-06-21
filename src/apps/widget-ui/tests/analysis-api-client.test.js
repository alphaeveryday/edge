import { describe, expect, it } from 'vitest';
import {
  createEmptyAnalysisResponse,
  findAnalysisFixtureBySymbol,
  getLatestAnalysis,
} from '../src/analysis-api-client.js';
import { ANALYSIS_V1_SUMMARY } from '../src/fixtures/analysis-response.fixture.js';

describe('S046 analysis API mock client', () => {
  it('getLatestAnalysis({ symbol: "005930" })가 005930.KS analysis fixture를 반환한다', async () => {
    const response = await getLatestAnalysis({ symbol: '005930' });
    expect(response.affected_assets).toHaveLength(1);
    expect(response.affected_assets[0].code).toBe('005930.KS');
    expect(response.affected_assets[0].summary).toBe(ANALYSIS_V1_SUMMARY);
  });

  it('getLatestAnalysis({ symbol: "005930.KS" })도 같은 fixture를 반환한다', async () => {
    const response = await getLatestAnalysis({ symbol: '005930.KS' });
    expect(response.affected_assets[0].code).toBe('005930.KS');
  });

  it('getLatestAnalysis({ symbol: "KRX:005930" })도 같은 fixture를 반환한다', async () => {
    const response = await getLatestAnalysis({ symbol: 'KRX:005930' });
    expect(response.affected_assets[0].code).toBe('005930.KS');
  });

  it('알 수 없는 symbol이면 empty analysis response를 반환한다', async () => {
    const response = await getLatestAnalysis({ symbol: '999999' });
    expect(response.affected_assets).toEqual([]);
    expect(response).toHaveProperty('as_of');
  });

  it('테스트용 error symbol이면 예외를 던진다', async () => {
    await expect(getLatestAnalysis({ symbol: 'THROW_ERROR' })).rejects.toThrow();
    await expect(getLatestAnalysis({ symbol: 'ERROR_TEST' })).rejects.toThrow();
  });

  it('tenantContext는 전달받아도 결과에 영향을 주지 않는다 (PoC: 권한/DB 조회 없음)', async () => {
    const withCtx = await getLatestAnalysis({
      symbol: '005930',
      tenantContext: { organizationId: 'org_demo_sec', embedKey: 'pub_demo_1234' },
    });
    const withoutCtx = await getLatestAnalysis({ symbol: '005930' });
    expect(withCtx).toEqual(withoutCtx);
  });

  it('findAnalysisFixtureBySymbol는 매칭 없으면 null, createEmptyAnalysisResponse는 빈 배열', () => {
    expect(findAnalysisFixtureBySymbol('999999')).toBeNull();
    expect(createEmptyAnalysisResponse('999999').affected_assets).toEqual([]);
  });
});
