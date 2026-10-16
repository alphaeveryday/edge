import { test, expect } from '@playwright/test';

const section = { paragraphs: ['합성 보고서 본문'], limitations: ['합성 자료의 한계'], evidence: [{ source_id: 'source-1', quote: '검증된 인용 문장', passage_id: 'passage-7' }] };
const evaluation = { scores: { leadership: 4, profitability: 4, stability: 4 }, overall: 4, reason: section };
const report = {
  sections: { opinion: section, outlook: section, question: section, portfolio: section },
  current_evaluation: evaluation, as_of: '2026-10-13T00:00:00Z',
  sources: [{ id: 'source-1', title: '합성 자료 제목', url: 'https://example.test/source', fetched_at: '2026-10-12T00:00:00Z' }],
  metrics: { total_krw: null, holdings: [], unavailable_reason: '자료 없음' },
  recommendation: { title: '합성 분산 추천', allocations: [{ instrument_id: 'US:TEST', name: 'Test ETF', ticker: 'TEST', account: 'ISA', weight_pct: 100, amount_krw: null }], evaluation,
    total_krw: null, actions: [{ instrument_id: 'US:TEST', name: 'Test ETF', ticker: 'TEST', account: 'ISA', before_pct: 0, after_pct: 100, action: '새로 담기' }],
    expected_return_1y: -2, dividend_yield_1y: 1, return_forecast: { reason: '합성 전망 이유', method: '합성 전망 계산' }, dividend_basis: '합성 배당 근거',
    historical_performance: { points: [{ date: '2025-10-13', recommended: 100, current: 100 }, { date: '2026-10-13', recommended: 105, current: 99 }], basis: '합성 과거 성과', source_url: 'https://example.test/history' } },
  insights: [{ title: '합성 인사이트', content: section }],
};

async function openDetail(page: import('@playwright/test').Page, result: unknown, followups: unknown[] = []) {
  await page.route('**/api/portfolio/admin/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/overview')) return route.fulfill({ json: { as_of: '2026-10-13T00:00:00Z', active_visitors: 1, today_visitors: 1, queued: 0, running: 0, today_jobs: {}, daily: [], today_unique_emails: 2, total_unique_emails: 4, total_report_requests: 6 } });
    if (path.endsWith('/records')) return route.fulfill({ json: { total: 1, page: 1, page_size: 50, items: [{ id: 'synthetic', visitor: 'synthetic', at: '2026-10-13T00:00:00Z', state: 'succeeded' }] } });
    return route.fulfill({ json: { id: 'synthetic', visitor: 'synthetic', state: 'succeeded', input: { holdings: [], question: '합성 고객 질문', preferences: {} }, result, followups } });
  });
  await page.goto('/portfolio/admin.html');
  await page.getByRole('button', { name: 'synthetic 상세 보기' }).click();
}

test('administrator keeps rebalance amounts readable inside the isolated customer report', async ({ page }) => {
  const execution = { status: 'ready', reasons: [], assumptions: [], additional_krw: '0', monthly_krw: null,
    required_additional_krw: null, months: null, accounts: [],
    rows: [{ instrument_id: 'US:VT', name: 'Vanguard Total World Stock Index ETF', account: '일반 계좌',
      before_krw: '0', target_krw: '500000', buy_krw: '500000', sell_krw: '0', cash_used_krw: '0' }] };
  await openDetail(page, { ...report, recommendation: { ...report.recommendation, execution } });
  const trades = page.locator('.execution-actions');
  await expect(trades.locator('.trade-amount')).toHaveText('500,000원매수');
  await expect(trades.locator('h5')).toHaveText('VT');
  await expect(trades.locator('.action-note')).toHaveText('일반 계좌');
  await expect(page.locator('.execution-plan table')).toHaveCount(0);
  expect(await trades.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.locator('.execution-plan').screenshot({ path: `test-results/admin-rebalance-${test.info().project.name}.png` });
});

test('administrator sees the complete customer report, separate evidence and expanded followups without changing dashboard styles', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await openDetail(page, report, [
    { input: { question: '추가 질문 전문\n두 번째 줄' }, result: { answer: { paragraphs: ['첫 번째 답변 문단', '두 번째 답변 문단'] } }, state: 'succeeded', created_at: '2026-10-13T00:12:00Z' },
    { input: { question: '대기 질문' }, result: null, state: 'queued', created_at: '2026-10-13T00:13:00Z' },
    { input: { question: '진행 질문' }, result: null, state: 'running', created_at: '2026-10-13T00:14:00Z' },
    { input: { question: '실패 질문' }, result: null, state: 'failed', failure: 'TIMED_OUT', created_at: '2026-10-13T00:15:00Z' },
  ]);
  for (const [label, value] of [['오늘 고유 이메일', '2'], ['누적 고유 이메일', '4'], ['리포트 신청 레코드', '6']]) {
    await expect(page.locator('.stat').filter({ hasText: label }).locator('strong')).toHaveText(value);
  }
  await expect(page.locator('#report .total b')).toHaveText('4');
  await expect(page.locator('#report .verdict em')).toHaveText('훌륭');
  await expect(page.locator('#report .verdict em')).not.toHaveCSS('background-image', 'none');
  await expect(page.getByText('합성 분산 추천', { exact: true })).toBeVisible();
  await expect(page.locator('#report .forecast b')).toHaveText(['-2%', '1%']);
  await expect(page.locator('#report .account-plan')).toContainText('ISA');
  await expect(page.getByText('TEST 새로 담기')).toBeVisible();
  await page.getByText('수익률·배당률 산정 근거', { exact: true }).click();
  await expect(page.getByText('합성 배당 근거', { exact: true })).toBeVisible();
  await expect(page.locator('#report .report-history svg')).toBeVisible();
  await expect(page.getByRole('heading', { name: '합성 인사이트' })).toBeVisible();
  await expect(page.getByText('첫 번째 답변 문단', { exact: true })).toBeVisible();
  await expect(page.getByText('두 번째 답변 문단', { exact: true })).toBeVisible();
  await expect(page.locator('.followup-record')).toHaveCount(4);
  await expect(page.locator('.followup-record').nth(1)).toContainText('답변 대기');
  await expect(page.locator('.followup-record').nth(2)).toContainText('답변 작성 중');
  await expect(page.locator('.followup-record').nth(3)).toContainText('TIMED_OUT');
  await expect(page.locator('.followup-record').first()).toContainText('KST');
  await expect(page.getByRole('button', { name: '질문 보내기' })).toHaveCount(0);
  await expect(page.locator('#report blockquote')).toHaveCount(0);
  await page.getByRole('tab', { name: '출처와 인용' }).click();
  await expect(page.getByText('합성 자료 제목', { exact: true })).toBeVisible();
  await expect(page.getByText('검증된 인용 문장').first()).toBeVisible();
  await expect(page.locator('.source-record code').filter({ hasText: 'passage-7' }).first()).toBeVisible();
  await expect(page.getByRole('link', { name: 'https://example.test/source', exact: true }).first()).toHaveAttribute('href', 'https://example.test/source');
  await page.getByRole('tab', { name: '고객 입력' }).click();
  await expect(page.getByText('합성 고객 질문', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '고객 결과' }).click();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('.sidebar')).toHaveCSS('background-color', 'rgb(21, 40, 34)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (test.info().project.name === 'desktop') expect((await page.locator('.detail').boundingBox())!.width).toBeGreaterThan(850);
  await page.screenshot({ path: `test-results/admin-report-${test.info().project.name}.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test('partial saved results and missing followups remain readable without invented numeric values or unsafe links', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await openDetail(page, { sections: { opinion: { paragraphs: ['오래된 결론'], evidence: [{ source_id: 'missing', quote: '<script>alert(1)</script>' }] } }, sources: [{ id: 'unsafe', url: 'javascript:alert(1)' }], recommendation: { title: '부분 추천' } });
  await expect(page.getByText('오래된 결론', { exact: true })).toBeVisible();
  await expect(page.getByText('추가 질문이 없습니다.')).toBeVisible();
  await expect(page.locator('.detail')).not.toContainText('NaN');
  await expect(page.locator('.detail')).not.toContainText('Invalid Date');
  await expect(page.locator('#report .total b')).toHaveCount(0);
  await page.getByRole('tab', { name: '출처와 인용' }).click();
  await expect(page.getByText('<script>alert(1)</script>', { exact: true })).toBeVisible();
  await expect(page.getByText('출처 정보 없음')).toBeVisible();
  await expect(page.locator('.detail a[href^="javascript:"]')).toHaveCount(0);
  await expect(page.getByText('quote_id', { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});

for (const missing of [null, undefined]) {
  test(`missing saved weights and history values (${missing}) must not imply a zero allocation or a total loss`, async ({ page }) => {
    const partial = structuredClone(report);
    Object.assign(partial.recommendation.allocations[0], { weight_pct: missing });
    Object.assign(partial.recommendation.historical_performance.points[1], { recommended: missing });
    await openDetail(page, partial);
    await expect(page.locator('#report .account-total strong')).toHaveText('비중 자료 없음');
    await expect(page.locator('#report .account-plan td strong')).toHaveText('비중 자료 없음');
    await expect(page.locator('#report .mixviz .donut')).toHaveCount(0);
    await expect(page.locator('#report .performance')).toContainText('저장된 성과 수치가 불완전');
    await expect(page.locator('#report .performance svg')).toHaveCount(0);
    await expect(page.locator('#report')).not.toContainText('NaN');
    await expect(page.locator('#report')).not.toContainText('undefined%');
  });
}

test('real zero allocation, amounts and history remain zero, while a missing current series is not drawn', async ({ page }) => {
  const zero = structuredClone(report);
  Object.assign(zero.recommendation.allocations[0], { weight_pct: 0, amount_krw: '0' });
  zero.recommendation.historical_performance.points.forEach(p => Object.assign(p, { recommended: 0, current: undefined }));
  await openDetail(page, zero);
  await expect(page.locator('#report .mixviz li>b')).toHaveText('0%');
  await expect(page.locator('#report .account-total strong')).toHaveText('0만 원');
  await expect(page.locator('#report .account-plan td strong')).toHaveText('0만 원');
  await expect(page.locator('#report .history-values b')).toHaveText(['0']);
  await expect(page.locator('#report .performance svg')).toBeVisible();
  await expect(page.locator('#report .history-line.a')).toHaveCount(0);
});
