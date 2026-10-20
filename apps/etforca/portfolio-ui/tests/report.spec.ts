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

test('small positive allocations remain visible in composition and account totals', async ({ page }) => {
  const small = structuredClone(report);
  small.recommendation.allocations[0] = { ...small.recommendation.allocations[0], weight_pct: .004, amount_krw: null as any };
  small.recommendation.allocations[1] = { ...small.recommendation.allocations[1], weight_pct: 99.996, account: 'ISA', amount_krw: null as any };
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'small'));
  await page.route('**/api/portfolio/analyses/small', route => route.fulfill({ json: { id: 'small', state: 'succeeded', result: small } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.mixviz li').first()).toContainText('0.01% 미만');
  await expect(page.locator('.account-plan').first().locator('.account-total')).toContainText('0.01% 미만');
});

test('evaluation labels reflow without clipping when report text is enlarged', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'large-type'));
  await page.route('**/api/portfolio/analyses/large-type', route => route.fulfill({ json: { id: 'large-type', state: 'succeeded', result: report } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.grade').first()).toBeVisible();
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  expect(await page.locator('.grade').first().evaluate(el => {
    const bounds = el.getBoundingClientRect();
    return [...el.children].every(child => { const r = child.getBoundingClientRect(); return r.left >= bounds.left && r.right <= bounds.right + 1 && child.scrollWidth <= child.clientWidth; });
  })).toBe(true);
  expect(await page.locator('.report-history svg text').evaluateAll(labels => labels.every(label => {
    const box = (label as SVGGraphicsElement).getBBox();
    const view = (label as SVGGraphicsElement).ownerSVGElement!.viewBox.baseVal;
    return box.x >= 0 && box.x + box.width <= view.width && box.y >= 0 && box.y + box.height <= view.height;
  }))).toBe(true);
  await expect(page.locator('.history-values')).toContainText('1,036,500원');
});

test('failed overwrite restores keyboard focus after the save button is enabled again', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'overwrite-failure'));
  await page.route('**/api/portfolio/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const workbook = { title: '기존 구성', fx: null, assets: [], dividends: [], months: [] };
    if (path === '/api/portfolio/workbook' && route.request().method() === 'POST') {
      await new Promise(resolve => setTimeout(resolve, 500));
      return route.fulfill({ status: 503, json: {} });
    }
    if (path === '/api/portfolio/workbook') return route.fulfill({ json: { version: 1, workbook } });
    if (path.endsWith('/workbook')) return route.fulfill({ json: workbook });
    if (path.endsWith('/overwrite-failure')) return route.fulfill({ json: { id: 'overwrite-failure', state: 'succeeded', result: report } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  const save = page.locator('.feedback-anchor>.save-portfolio>button');
  await save.click();
  await expect(page.locator('.feedback-anchor .save-portfolio [role=alert]')).toBeVisible();
  await expect(save).toBeEnabled();
  await expect(save).toBeFocused();
});

for (const fallbackFont of [false, true]) test(`large balances with small changes keep distinct, unclipped performance ticks (fallback font: ${fallbackFont})`, async ({ page }) => {
  if (fallbackFont) await page.route('**/*.woff2', route => route.abort());
  const result = structuredClone(report);
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'precise-ticks'));
  await page.route('**/api/portfolio/analyses/precise-ticks', route => route.fulfill({ json: { id: 'precise-ticks', state: 'succeeded', result } }));
  for (const difference of [1000000, 1]) {
    result.recommendation.historical_performance.points = Array.from({ length: 3 }, (_, i) => ({ date: `2026-10-${10 + i}`, recommended: 101000000 + difference * i / 2, current: 101000000 }));
    await page.goto('/portfolio/');
    const ticks = page.locator('.report-history svg g text');
    await expect(ticks).toHaveCount(3);
    expect(new Set(await ticks.allTextContents()).size).toBe(3);
    await page.evaluate(() => document.fonts.ready);
    await expect.poll(() => ticks.evaluateAll(nodes => nodes.every(node => (node as SVGGraphicsElement).getBBox().x >= 0))).toBe(true);
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    await expect.poll(() => ticks.evaluateAll(nodes => nodes.every(node => (node as SVGGraphicsElement).getBBox().x >= 0))).toBe(true);
    await page.locator('.report-history').evaluate(el => { el.style.width = '240px'; });
    await expect.poll(async () => (await page.locator('.report-history svg').getAttribute('viewBox'))?.split(' ')[3]).toBe('180');
    expect(await page.locator('.report-history svg polyline.b').evaluate(node => {
      const points = (node as SVGPolylineElement).points;
      return points.getItem(points.numberOfItems - 1).x - points.getItem(0).x;
    })).toBeGreaterThanOrEqual(80);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect.poll(() => page.locator('.report-history svg>text').evaluateAll(nodes => nodes.every((node, i) => {
      if (!i) return true;
      const previous = (nodes[i - 1] as SVGGraphicsElement).getBBox(), current = (node as SVGGraphicsElement).getBBox();
      return previous.x + previous.width + 4 <= current.x;
    }))).toBe(true);
  }
});

test('rebalance instructions show cash amounts and hide transactions when execution is infeasible', async ({ page }) => {
  const execution = { status: 'ready', reasons: [], assumptions: ['입력 평가금액 기준이에요.'],
    additional_krw: '0', monthly_krw: null, required_additional_krw: null, months: null,
    accounts: [{ account: '일반 계좌', deposit_krw: '0', withdrawal_krw: '0' }],
    rows: [{ instrument_id: 'US:QQQ', name: 'Invesco QQQ', account: '일반 계좌', before_krw: '0',
      target_krw: '600000', buy_krw: '600000', sell_krw: '0', cash_used_krw: '0' },
      { instrument_id: 'CASH:KRW', name: '현금', account: '일반 계좌', before_krw: '200000',
        target_krw: '0', buy_krw: '0', sell_krw: '0', cash_used_krw: '200000' },
      { instrument_id: 'US:VOO', name: 'Vanguard S&P 500 ETF', account: '일반 계좌', before_krw: '1000000', target_krw: '600000', buy_krw: '0', sell_krw: '400000', cash_used_krw: '0' }] };
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'execution'));
  await page.route('**/api/portfolio/analyses/execution', route => route.fulfill({ json: {
    id: 'execution', state: 'succeeded', result: { ...report, recommendation: { ...report.recommendation, execution } }
  } }));
  await page.goto('/portfolio/');
  const trades = page.locator('.improvements .improvement-list');
  await expect(page.locator('.execution-plan table')).toHaveCount(0);
  await expect(trades.locator('li').filter({ hasText: 'QQQ' })).toContainText('600,000원매수');
  await expect(trades.locator('li').filter({ hasText: 'VOO' })).toContainText('400,000원매도');
  await expect(trades.locator('li').filter({ hasText: '현금' })).toContainText('200,000원현금 투입');
  await expect(trades.locator('[data-trade="buy"]')).toHaveCSS('color', 'rgb(210, 32, 48)');
  await expect(trades.locator('[data-trade="sell"]')).toHaveCSS('color', 'rgb(27, 100, 218)');
  await expect(page.locator('.improvements .trade-amount')).toHaveText(['600,000원매수', '200,000원현금 투입', '400,000원매도']);
  await expect(page.getByRole('heading', { name: '내 포트폴리오에서 바꿀 것' })).toHaveCount(1);
  await expect(page.getByRole('heading', { name: '매수·매도 계획' })).toHaveCount(0);
  await expect(page.locator('.improvements .weight-change')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator('.improvements').screenshot({ path: `test-results/rebalance-${test.info().project.name}.png` });
  execution.rows[0].buy_krw = '10000000000';
  await page.reload();
  await expect(page.locator('.improvements [data-trade=buy]')).toContainText('10,000,000,000원');
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  expect(await page.locator('.improvements [data-trade=buy]').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  execution.rows[0].buy_krw = '600000';
  execution.status = 'infeasible';
  execution.reasons = ['NO_SELL_ZERO_TARGET'];
  await page.reload();
  await expect(page.locator('.improvements')).toContainText('이미 보유한 종목의 목표를 0%로 만들려면 매도가 필요해요.');
  await expect(page.locator('.improvements .trade-amount')).toHaveCount(0);
  await expect(page.locator('.improvements .trade-amount')).toHaveCount(0);
  await expect(page.locator('.improvements .weight-change')).toContainText('60%');
  // Older non-actionable reports may not have saved execution rows at all.
  execution.status = 'needs_input';
  Object.assign(execution, { rows: undefined });
  await page.reload();
  await expect(page.locator('.improvements .weight-change')).toContainText('60%');
  await expect(page.locator('.improvements .trade-amount')).toHaveCount(0);
});

test('saved trade amounts take priority over weight direction and monthly totals stay explicit', async ({ page }) => {
  const result = structuredClone(report);
  result.recommendation.actions[0].before_pct = 80;
  result.recommendation.actions[0].action = '줄이기';
  Object.assign(result.recommendation.actions[0], { buy_krw: '300000', sell_krw: '0', cash_used_krw: '0' });
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'trade-priority'));
  await page.route('**/api/portfolio/analyses/trade-priority', route => route.fulfill({ json: { id: 'trade-priority', state: 'succeeded', result } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.improvements .trade-amount')).toHaveText('300,000원매수');
  await expect(page.locator('.improvements [data-trade="buy"]')).toHaveCSS('color', 'rgb(210, 32, 48)');
  await expect(page.locator('.improvements')).not.toContainText('줄이기');
  Object.assign(result.recommendation, { execution: { status: 'conditional', reasons: [], assumptions: [],
    monthly_krw: '50000', months: 6, additional_krw: '300000', required_additional_krw: '300000', accounts: [],
    rows: [{ instrument_id: 'US:QQQ', name: 'QQQ', account: '일반 계좌', before_krw: '800000', target_krw: '1100000', buy_krw: '300000', sell_krw: '0', cash_used_krw: '0' }] } });
  await page.reload();
  await expect(page.locator('.improvements')).toContainText('6개월 계획의 총 거래 금액');
  await expect(page.locator('.improvements')).toContainText('매월 50,000원씩 6개월');
  await expect(page.locator('.funding-summary')).toContainText('300,000원 투입');
  await expect(page.locator('.improvements .trade-amount')).toHaveText('300,000원매수');
  Object.assign((result.recommendation as unknown as { execution: Record<string, unknown> }).execution, { status: 'needs_input', reasons: ['ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED:ISA'] });
  await page.reload();
  await expect(page.locator('.funding-summary')).toContainText('계산에 사용한 추가 현금 300,000원');
  await expect(page.locator('.funding-summary')).toContainText('매월 50,000원씩 6개월');
  await expect(page.locator('.improvements .trade-amount')).toHaveCount(0);
});

test('a lump-sum cash deposit and execution-only trades appear in the single changes card', async ({ page }) => {
  const result = structuredClone(report);
  result.recommendation.actions = [];
  Object.assign(result.recommendation, { execution: { status: 'ready', reasons: [], assumptions: [],
    monthly_krw: null, months: null, additional_krw: '200000', required_additional_krw: null,
    accounts: [{ account: '일반 계좌', deposit_krw: '200000', withdrawal_krw: '0' }],
    rows: [{ instrument_id: 'US:QQQ', name: 'QQQ', account: '일반 계좌', before_krw: '400000', target_krw: '600000', buy_krw: '200000', sell_krw: '0', cash_used_krw: '0' }] } });
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'cash-deposit'));
  await page.route('**/api/portfolio/analyses/cash-deposit', route => route.fulfill({ json: { id: 'cash-deposit', state: 'succeeded', result } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.improvements')).toHaveCount(1);
  await expect(page.locator('.improvements .trade-amount')).toHaveText('200,000원매수');
  await expect(page.locator('.funding-summary')).toHaveText('추가 현금 200,000원 투입');
  await expect(page.locator('.improvements')).not.toContainText('현재 구성을 유지');
  await expect(page.locator('.execution-plan')).toHaveCount(0);
});

for (const invalid of [undefined, '']) {
  test(`weights remain percentages without usable trade amounts (${invalid})`, async ({ page }) => {
    const result = structuredClone(report);
    Object.assign(result.metrics, { total_krw: null });
    Object.assign(result.recommendation, { total_krw: null });
    result.recommendation.allocations.forEach(a => Object.assign(a, { amount_krw: null }));
    Object.assign(result.recommendation.actions[0], { before_pct: 80, buy_krw: invalid, sell_krw: invalid, cash_used_krw: invalid });
    await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'weight-only'));
    await page.route('**/api/portfolio/analyses/weight-only', route => route.fulfill({ json: { id: 'weight-only', state: 'succeeded', result } }));
    await page.goto('/portfolio/');
    await expect(page.locator('.improvements .weight-change')).toHaveText('80%→60%');
    await expect(page.locator('.improvements .trade-amount')).toHaveCount(0);
    await expect(page.locator('.improvements')).not.toContainText('원');
    await expect(page.locator('#report')).not.toContainText('NaN');
  });
}

test('the same ETF in different accounts keeps its own trade and cash is not a sale', async ({ page }) => {
  const result = structuredClone(report);
  result.recommendation.actions = ['ISA', '일반 계좌'].map(account => ({ ...report.recommendation.actions[0], instrument_id: 'KR:069500', name: 'KODEX 200', ticker: '069500', account }));
  Object.assign(result.recommendation, { execution: { status: 'ready', reasons: [], assumptions: [],
    monthly_krw: null, months: null, additional_krw: '0', required_additional_krw: null, accounts: [], rows: [
      { instrument_id: 'KR:069500', name: 'KODEX 200', account: '일반 계좌', before_krw: '100000', target_krw: '70000', buy_krw: '0', sell_krw: '30000', cash_used_krw: '0' },
      { instrument_id: 'KR:069500', name: 'KODEX 200', account: 'ISA', before_krw: '200000', target_krw: '250000', buy_krw: '50000', sell_krw: '0', cash_used_krw: '0' },
      { instrument_id: 'CASH:KRW', name: '현금', account: '일반 계좌', before_krw: '0', target_krw: '30000', buy_krw: '0', sell_krw: '0', cash_used_krw: '-30000' },
      { instrument_id: 'US:VT', name: 'VT', account: '일반 계좌', before_krw: '100000', target_krw: '100000', buy_krw: '0', sell_krw: '0', cash_used_krw: '0' },
    ] } });
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'accounts'));
  await page.route('**/api/portfolio/analyses/accounts', route => route.fulfill({ json: { id: 'accounts', state: 'succeeded', result } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.improvements .trade-amount')).toHaveText(['50,000원매수', '30,000원매도', '30,000원현금 확보', '보유 유지']);
  await expect(page.locator('.improvements .action-note')).toHaveText(['ISA', '일반 계좌', '일반 계좌', '일반 계좌']);
  const cash = page.locator('.improvements li').filter({ hasText: '현금' });
  await expect(cash).toContainText('30,000원현금 확보');
  await expect(cash.locator('[data-trade="sell"]')).toHaveCount(0);
  await expect(page.locator('.improvements li').filter({ hasText: 'VT' })).toContainText('보유 유지');
});

test('an execution rejection explains funding or account constraints without blaming ratings', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'blocked-execution'));
  await page.route('**/api/portfolio/analyses/blocked-execution', route => route.fulfill({ json: {
    id: 'blocked-execution', state: 'succeeded', result: { ...report, recommendation: null,
      optimization: { status: 'available', reason: 'EXECUTION_INFEASIBLE' } }
  } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.ans:not(.lead)')).toContainText('매도·추가 자금 또는 계좌 한도를 만족하는 실행 방법');
  await expect(page.locator('.ans:not(.lead)')).not.toContainText('종합 4점');
});

test('optional research gaps preserve a clear recommendation while unfinished questions remain visible', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'partial'));
  await page.route('**/api/portfolio/analyses/partial', route => route.fulfill({ json: {
    id: 'partial', state: 'succeeded', result: { ...report, completion: { status: 'partial',
      tasks: [{ question: '어떻게 리밸런싱하나요?', status: 'partial', answer: '', missing: ['QUESTION_NOT_ANSWERED'] }],
      missing_inputs: [], missing_research: [{ instrument_id: 'US:QQQ', field: 'holdings', status: 'stale' }] } }
  } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.completion-summary')).toContainText('이 질문에 답하기 위한 확인이 남아 있어요.');
  await expect(page.locator('.headrow').first()).toContainText('분석 결론');
  await expect(page.locator('.recommendation-label')).toHaveText('추천 포트폴리오');
  await page.getByText('추가로 확인할 상품 자료', { exact: true }).click();
  await expect(page.locator('.completion-summary')).toContainText('Invesco QQQ · 보유내역 · 최신 자료 필요');
});

test('missing optional yields stay blank and input codes never appear to customers', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'optional-yields'));
  await page.route('**/api/portfolio/analyses/optional-yields', route => route.fulfill({ json: {
    id: 'optional-yields', state: 'succeeded', result: { ...report,
      recommendation: { ...report.recommendation, expected_return_1y: null, dividend_yield_1y: null },
      completion: { status: 'needs_input', tasks: [], missing_research: [],
        missing_inputs: ['ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED:ISA', 'INTERNAL_UNKNOWN_REASON', '요청 조건 확인: NASDAQ_100 비중을 확인해 주세요'] } }
  } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.completion-summary')).toContainText('ISA의 남은 납입 가능 금액');
  await expect(page.locator('.completion-summary')).toContainText('NASDAQ_100 비중을 확인해 주세요');
  await expect(page.locator('#report')).not.toContainText('ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED');
  await expect(page.locator('#report')).not.toContainText('INTERNAL_UNKNOWN_REASON');
  await expect(page.locator('.forecast')).toHaveCount(0);
  await expect(page.locator('.recommendation-label')).toBeVisible();
});

test('missing allocation weights are distinguished from missing market prices', async ({ page }) => {
  let reason = 'CURRENT_WEIGHTS_REQUIRED';
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'specific-reason'));
  await page.route('**/api/portfolio/analyses/specific-reason', route => route.fulfill({ json: {
    id: 'specific-reason', state: 'succeeded', result: { ...report, recommendation: null,
      optimization: { status: 'unavailable', reason } }
  } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.ans:not(.lead)')).toContainText('종목별 평가금액 또는 보유 비중');
  reason = 'NO_COMMON_PRICE_HISTORY';
  await page.reload();
  await expect(page.locator('.ans:not(.lead)')).toContainText('겹치는 가격 이력');
  await expect(page.locator('.ans:not(.lead)')).not.toContainText('평가금액 또는 보유 비중');
});

test('a newly listed ETF displays both series over their actual common period', async ({ page }) => {
  const history = { ...report.recommendation.historical_performance,
    basis: '2026-07-07 ~ 2026-10-09 · 공통 자료 기간 · 100만 원을 넣었다면 (원)',
    points: [{ date: '2026-07-07', recommended: 1000000, current: 1000000 },
      { date: '2026-10-09', recommended: 1100000, current: 950000 }] };
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'short-history'));
  await page.route('**/api/portfolio/analyses/short-history', route => route.fulfill({ json: {
    id: 'short-history', state: 'succeeded', result: { ...report,
      recommendation: { ...report.recommendation, historical_performance: history } }
  } }));
  await page.goto('/portfolio/');
  await expect(page.getByRole('img', { name: '2026-07-07부터 2026-10-09까지 추천 구성과 현재 구성의 과거 성과' })).toBeVisible();
  await expect(page.locator('.report-history figcaption')).toHaveText(history.basis);
  await expect(page.locator('.history-line')).toHaveCount(2);
  await expect(page.locator('.report-history')).not.toContainText('최근 1년');
});

test('new portfolio history labels its hypothetical cash baseline explicitly', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'new-cash'));
  await page.route('**/api/portfolio/analyses/new-cash', route => route.fulfill({ json: {
    id: 'new-cash', state: 'succeeded', result: { ...report,
      recommendation: { ...report.recommendation, historical_performance: {
        ...report.recommendation.historical_performance, baseline_kind: 'new_cash' } } }
  } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.history-legend')).toContainText('투자 전 현금');
  await expect(page.locator('.history-values')).toContainText('투자 전 현금');
  await expect(page.locator('.history-legend')).not.toContainText('현재 구성');
});

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
  await expect(page.getByText('검토한 후보 중 시장 주도성·수익성·안정성 평가의 추천 기준을 충족한 구성을 찾지 못했어요.')).toBeVisible();
  await expect(page.locator('.portfolio-hero')).toHaveCount(0);
});

test('customer constraint rejection is not mislabeled as a low star rating', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'customer-constraint'));
  await page.route('**/api/portfolio/analyses/customer-constraint', route => route.fulfill({ json: {
    id: 'customer-constraint', state: 'succeeded', result: { ...report, recommendation: null,
      optimization: { status: 'available', candidates: [{}], reason: 'CUSTOMER_CONSTRAINT_NOT_SATISFIED' } }
  } }));
  await page.goto('/portfolio/');
  await expect(page.getByText('검토한 후보에서 요청하신 필수 조건을 모두 만족하는 구성을 찾지 못했어요.')).toBeVisible();
  await expect(page.getByText('검토한 후보 중 시장 주도성·수익성·안정성 평가의 추천 기준을 충족한 구성을 찾지 못했어요.')).toHaveCount(0);
});

test('an unselected recommendation does not invent a constraint or rating failure', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'withheld'));
  await page.route('**/api/portfolio/analyses/withheld', route => route.fulfill({ json: {
    id: 'withheld', state: 'succeeded', result: { ...report, recommendation: null,
      optimization: { status: 'available', candidates: [{}], reason: 'RECOMMENDATION_WITHHELD' } }
  } }));
  await page.goto('/portfolio/');
  await expect(page.getByText('이번 분석에서 추천 구성을 완성하지 못했어요.')).toBeVisible();
  await expect(page.getByText('검토한 후보에서 요청하신 필수 조건을 모두 만족하는 구성을 찾지 못했어요.')).toHaveCount(0);
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
  await expect(page.locator('.forecast')).toHaveCount(0);
  await expect(page.locator('.performance')).toContainText('검증된 자료 부족');
  await expect(page.locator('.performance svg')).toHaveCount(0);
});

test('neutral report surfaces keep financial labels readable at both viewport sizes', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'design'));
  await page.route('**/api/portfolio/analyses/design', route => route.fulfill({ json: { id: 'design', state: 'succeeded', result: report } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.portfolio-hero')).toBeVisible();
  for (const selector of ['.recommendation-label', '.forecast span', '.forecast b', '.account-plan td strong', '.recommended-axes b', '.recommended-axes em', '.rating-denominator', '.history-date', '.report-chat button']) {
    const ratio = await page.locator(selector).first().evaluate(node => {
      const luminance = (color: string) => {
        const channels = color.match(/[\d.]+/g)!.slice(0, 3).map(v => Number(v) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
        return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
      };
      let surface: Element | null = node;
      while (surface && getComputedStyle(surface).backgroundColor === 'rgba(0, 0, 0, 0)') surface = surface.parentElement;
      const a = luminance(getComputedStyle(node).color), b = luminance(getComputedStyle(surface!).backgroundColor);
      return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    });
    expect(ratio, selector).toBeGreaterThanOrEqual(4.5);
  }
  await expect(page.locator('.portfolio-hero')).toHaveCSS('background-image', 'none');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
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

test('interrupted analysis exposes saved work without publishing its draft recommendation', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'interrupted'));
  await page.route('**/api/portfolio/analyses/interrupted', route => route.fulfill({ json: {
    id: 'interrupted', state: 'failed', failure: 'WORKER_INTERRUPTED', result: { partial_report: { ...report,
      completion: { status: 'partial', tasks: [{ question: '매수 금액은 얼마인가요?', status: 'partial', answer: '납입 가능 금액을 확인하고 있어요.', missing: [] }],
        missing_research: [], missing_inputs: ['현재 납입 가능 금액을 확인해야 해요.'] } } },
  } }));
  await page.goto('/portfolio/');
  await page.getByText('중단 전 작성된 분석 보기').click();
  await expect(page.locator('.completion-summary')).toBeVisible();
  await expect(page.locator('.completion-summary')).toContainText('매수 금액은 얼마인가요?');
  await expect(page.locator('.completion-summary')).toContainText('현재 납입 가능 금액을 확인해야 해요.');
  await expect(page.locator('.portfolio-hero')).toHaveCount(0);
});
