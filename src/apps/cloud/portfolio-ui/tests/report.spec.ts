import { test, expect } from '@playwright/test';

const section = { paragraphs: ['사용자의 투자 기간을 기준으로 판단해요.', '추천 구성에 영향을 주는 근거를 확인해요.', '전망이 달라지면 추천 비중도 다시 검토해요.'], evidence: [], limitations: [] };
const evaluation = { scores: { leadership: 5, profitability: 4, stability: 3 }, overall: 4, reason: section, method: 'AI 정성 평가', rubric_version: 1 };
const report = {
  schema_version: 2, sections: { opinion: section, outlook: section, question: section, portfolio: section },
  sources: [], as_of: '2026-10-12T00:00:00Z', current_evaluation: evaluation,
  metrics: { total_krw: '1000000', holdings: [{ name: '이전 VOO', account: '일반 계좌', weight_pct: 100 }], unavailable_reason: '검증된 자료 부족' },
  recommendation: { title: '분산 구성', evaluation, allocations: [
    { instrument_id: 'US:QQQ', name: 'Invesco QQQ', ticker: 'QQQ', kind: 'etf', account: '일반 계좌', weight_pct: 60, amount_krw: '600000' },
    { instrument_id: 'CASH:KRW', name: '현금', ticker: 'KRW', kind: 'cash', account: '일반 계좌', weight_pct: 40, amount_krw: '400000' }],
    total_krw: '1000000', actions: [{ instrument_id: 'US:QQQ', name: 'Invesco QQQ', ticker: 'QQQ', account: '일반 계좌', before_pct: 0, after_pct: 60, action: '새로 담기' }],
    expected_return_1y: -3, dividend_yield_1y: .8, unavailable_reason: '검증된 자료 부족',
    historical_performance: { basis: '최근 1년 · 100만 원을 넣었다면 (원)', source_url: 'https://example.com/history', interval: '1d', currency: 'KRW', points: Array.from({ length: 366 }, (_, i) => ({ date: new Date(Date.UTC(2025, 9, 12 + i)).toISOString().slice(0, 10), recommended: 1000000 + i * 100, current: 1000000 - i * 100 })) } },
  insights: [{ title: '결혼 자금은 따로 확보하세요', content: section }]
};

test('a cash-only current portfolio does not hide the recommended total and three star ratings', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'cash-rating'));
  await page.route('**/api/portfolio/analyses/cash-rating', route => route.fulfill({ json: { id: 'cash-rating', state: 'succeeded', result: {
    ...report, current_evaluation: { ...evaluation, scores: null, overall: null }
  } } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.recommended-rating b')).toHaveText('4');
  await expect(page.locator('.recommended-axes .stars')).toHaveCount(3);
  await expect(page.locator('.recommended-axes .pending-score')).toHaveCount(0);
});

test('older scores are not relabeled as having used the restored rubric', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'old-rubric'));
  await page.route('**/api/portfolio/analyses/old-rubric', route => route.fulfill({ json: { id: 'old-rubric', state: 'succeeded', result: {
    ...report, current_evaluation: { ...evaluation, rubric_version: undefined }
  } } }));
  await page.goto('/portfolio/');
  await page.getByLabel('판단 기준 보기').click();
  await expect(page.locator('.crit')).toContainText('이전 평가 기준');
  await expect(page.locator('.crit')).not.toContainText('사용자의 선호는 점수에 넣지');
});

test('withholding a backtest winner below the rating threshold explains rating rather than missing prices', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'rating-withheld'));
  await page.route('**/api/portfolio/analyses/rating-withheld', route => route.fulfill({ json: { id: 'rating-withheld', state: 'succeeded', result: {
    ...report, recommendation: null, optimization: { status: 'available', reason: 'RATING_BELOW_MINIMUM', selected_candidate_id: null }
  } } }));
  await page.goto('/portfolio/');
  await expect(page.getByText('조사한 근거로 종합 4점 이상을 줄 수 있는 구성을 찾지 못해 추천을 보류했어요.')).toBeVisible();
  await expect(page.locator('.portfolio-hero')).toHaveCount(0);
});

test('a batch without eligible candidates publishes analysis without a recommendation or invented chart', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'no-match'));
  await page.route('**/api/portfolio/analyses/no-match', route => route.fulfill({ json: { id: 'no-match', state: 'succeeded',
    result: { ...report, schema_version: 3, recommendation: null, optimization: { status: 'no_match', selected_candidate_id: null } } } }));
  await page.goto('/portfolio/');
  await expect(page.getByText('이번 비교에서 추천 기준을 충족한 구성을 찾지 못했어요.')).toBeVisible();
  await expect(page.locator('.portfolio-hero')).toHaveCount(0);
  await expect(page.locator('.performance')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '핵심 평가 정리' })).toBeVisible();
});

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
  await expect(page.locator('.portfolio-hero .axes .stars')).toHaveCount(3);
  await expect(page.locator('.portfolio-hero .axes .stars').first()).toBeVisible();
  await page.getByLabel('판단 기준 보기').click();
  await expect(page.locator('.crit')).toContainText('시가총액 상위 기업');
  await expect(page.locator('.crit')).toContainText('나쁜 해의 하락 폭');
  await expect(page.locator('.crit')).not.toContainText('AI 정성 평가');
  await page.getByLabel('판단 기준 보기').click();
  await expect(page.locator('.forecast b')).toHaveText(['-3%', '0.8%']);
  await expect(page.locator('.evaluation__part h4')).toHaveText(['최종 투자 의견과 실행할 행동', '앞으로의 전망', '결혼 자금은 따로 확보하세요']);
  await expect(page.locator('.evaluation__part').first().locator('li')).toHaveCount(3);
  await expect(page.locator('.donut-enter circle').first()).toHaveCSS('animation-name', 'donut-reveal');
  await expect(page.locator('.report-history')).not.toHaveClass(/is-visible/);
  await page.locator('.report-history').scrollIntoViewIfNeeded();
  await expect(page.locator('.report-history')).toHaveClass(/is-visible/);
  await expect(page.locator('.report-history svg text').last()).toHaveCSS('text-anchor', 'end');
  expect((await page.locator('polyline.b').getAttribute('points'))!.split(' ')).toHaveLength(366);
  await page.getByRole('slider', { name: '과거 성과 날짜 선택' }).fill('100');
  await expect(page.locator('.history-values')).toContainText('1,010,000원');
  await expect(page.locator('.history-values')).toContainText('990,000원');
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

test('approved HTML typography, spacing and card styling match at the same viewport', async ({ page, context }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'design'));
  await page.route('**/api/portfolio/analyses/design', route => route.fulfill({ json: { id: 'design', state: 'succeeded', result: report } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.portfolio-hero')).toBeVisible();
  const reference = await context.newPage();
  await reference.goto('/portfolio/tests/reference/report.html');
  const selectors = ['.ans.lead', '.verdict em', '.total b', '.lead .axes b', '.portfolio-hero', '.portfolio-hero .todo', '.donut.md', '.mixviz li>b', '.recommended-rating b', '.forecast b', '.account-plan', '.account-plan h5', '.account-plan th[scope="row"]', '.account-plan td strong'];
  const properties = ['font-family', 'font-size', 'font-weight', 'line-height', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left', 'border-radius', 'background-image', 'background-color'];
  for (const selector of selectors) {
    const style = async (p: typeof page) => p.locator(selector).first().evaluate((node, props) => Object.fromEntries(props.map(prop => [prop, getComputedStyle(node).getPropertyValue(prop)])), properties);
    expect(await style(page), selector).toEqual(await style(reference));
  }
  for (const selector of ['.ans.lead', '.portfolio-hero', '.donut.md', '.account-plan']) {
    const actual = await page.locator(selector).first().boundingBox();
    const expected = await reference.locator(selector).first().boundingBox();
    expect(actual!.width, selector).toBeCloseTo(expected!.width, 0);
  }
  await reference.screenshot({ path: `test-results/reference-report-${test.info().project.name}.png`, fullPage: true });
  await reference.close();
});

test('legacy index values keep their units and account transfers stay identifiable', async ({ page }) => {
  const legacy = structuredClone(report);
  Object.assign(legacy.recommendation.historical_performance, { currency: undefined, interval: undefined, basis: '기준 100 지수', points: [
    { date: '2022-01-01', recommended: 100, current: 100 }, { date: '2024-01-01', recommended: 110, current: 108 }] });
  legacy.recommendation.actions = [
    { ...legacy.recommendation.actions[0], name: '국내 ETF', instrument_id: 'KR:069500', ticker: '069500', account: 'ISA', before_pct: 50, after_pct: 0, action: '줄이기' },
    { ...legacy.recommendation.actions[0], name: '국내 ETF', instrument_id: 'KR:069500', ticker: '069500', account: '일반 계좌', before_pct: 0, after_pct: 50, action: '늘리기' }];
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'legacy-chart'));
  await page.route('**/api/portfolio/analyses/legacy-chart', route => route.fulfill({ json: { id: 'legacy-chart', state: 'succeeded', result: legacy } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.history-values')).toContainText('110');
  await expect(page.locator('.history-values')).not.toContainText('원');
  await expect(page.locator('.action-note')).toHaveText(['ISA', '일반 계좌']);
});
