import { test, expect } from '@playwright/test';

const section = { paragraphs: ['사용자의 투자 기간을 기준으로 판단해요.', '추천 구성에 영향을 주는 근거를 확인해요.', '전망이 달라지면 추천 비중도 다시 검토해요.'], evidence: [], limitations: [] };
const evaluation = { scores: { leadership: 5, profitability: 4, stability: 3 }, overall: 4, reason: section, method: 'AI 정성 평가' };
const report = {
  schema_version: 2, sections: { opinion: section, outlook: section, question: section, portfolio: section },
  sources: [], as_of: '2026-10-12T00:00:00Z', current_evaluation: evaluation,
  metrics: { total_krw: '1000000', holdings: [{ name: '이전 VOO', account: '일반 계좌', weight_pct: 100 }], unavailable_reason: '검증된 자료 부족' },
  recommendation: { title: '분산 구성', evaluation, allocations: [
    { instrument_id: 'US:QQQ', name: 'Invesco QQQ', ticker: 'QQQ', kind: 'etf', account: '일반 계좌', weight_pct: 60, amount_krw: '600000' },
    { instrument_id: 'CASH:KRW', name: '현금', ticker: 'KRW', kind: 'cash', account: '일반 계좌', weight_pct: 40, amount_krw: '400000' }],
    total_krw: '1000000', actions: [{ instrument_id: 'US:QQQ', name: 'Invesco QQQ', ticker: 'QQQ', account: '일반 계좌', before_pct: 0, after_pct: 60, action: '새로 담기' }],
    expected_return_1y: -3, dividend_yield_1y: .8, unavailable_reason: '검증된 자료 부족',
    historical_performance: { basis: '테스트용 검증 시계열 · 기준 100', source_url: 'https://example.com/history', points: [
      { date: '2022-01', recommended: 100, current: 100 }, { date: '2023-01', recommended: 90, current: 95 }, { date: '2024-01', recommended: 110, current: 108 }] } },
  insights: [{ title: '결혼 자금은 따로 확보하세요', content: section }]
};

test('recommended holdings and final amounts are distinct from input; opinion then outlook then free insights', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'v2'));
  await page.route('**/api/portfolio/analyses/v2', route => route.fulfill({ json: { id: 'v2', state: 'succeeded', result: report } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.portfolio-hero')).toContainText('QQQ');
  await expect(page.locator('.portfolio-hero')).not.toContainText('이전 VOO');
  await expect(page.locator('.account-plan td')).toHaveText(['60만 원', '40만 원']);
  await expect(page.locator('.recommended-rating b')).toHaveText('4');
  await expect(page.locator('.portfolio-hero .axes li')).toHaveCount(3);
  await expect(page.locator('.forecast b')).toHaveText(['-3%', '0.8%']);
  await expect(page.locator('.evaluation__part h4')).toHaveText(['최종 투자 의견과 실행할 행동', '앞으로의 전망', '결혼 자금은 따로 확보하세요']);
  await expect(page.locator('.evaluation__part').first().locator('li')).toHaveCount(3);
  await expect(page.locator('.donut-enter circle').first()).toHaveCSS('animation-name', 'donut-reveal');
  await expect(page.locator('.report-history')).not.toHaveClass(/is-visible/);
  await page.locator('.report-history').scrollIntoViewIfNeeded();
  await expect(page.locator('.report-history')).toHaveClass(/is-visible/);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('.donut-enter circle').first()).toHaveCSS('animation-name', 'none');
  await expect(page.locator('.history-line').first()).toHaveCSS('stroke-dashoffset', '0px');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/report-v2-${test.info().project.name}.png`, fullPage: true });
});

test('unknown budget and unconnected history do not fabricate amounts or performance', async ({ page }) => {
  const unavailable = structuredClone(report);
  Object.assign(unavailable.recommendation, { total_krw: null, expected_return_1y: null, dividend_yield_1y: null, historical_performance: null });
  unavailable.recommendation.allocations.forEach(h => Object.assign(h, { amount_krw: null }));
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'unknown'));
  await page.route('**/api/portfolio/analyses/unknown', route => route.fulfill({ json: { id: 'unknown', state: 'succeeded', result: unavailable } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.account-plan td')).toHaveText(['60%', '40%']);
  await expect(page.locator('.forecast b')).toHaveText(['산출 전', '산출 전']);
  await expect(page.locator('.performance')).toContainText('검증된 자료 부족');
  await expect(page.locator('.performance svg')).toHaveCount(0);
});
