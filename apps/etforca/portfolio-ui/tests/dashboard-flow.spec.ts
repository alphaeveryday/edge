import { test, expect, type Page } from '@playwright/test';
import type { Asset, Saved, Workbook } from '../src/workbook';

const asset: Asset = { id: 'holding', instrument_id: 'KR:069500', name: 'KODEX 200', account: 'ISA', category: '주식', currency: 'KRW', quantity: null, amount: 1000000, weight: null, average: null, price: null, cost_fx: null, target: null, fee: 0, price_date: null };
const workbook: Workbook = { title: '내 포트폴리오', assets: [asset], dividends: [], months: [], fx: null };
const section = { paragraphs: ['내 분석 결과'], evidence: [], limitations: [] };
const job = { id: 'flow', state: 'succeeded', result: { sections: { question: section, portfolio: section, outlook: section, opinion: section }, metrics: { holdings: [], total_krw: '1000000', unavailable_reason: '' }, sources: [], as_of: '2026-10-19', model: 'test', elapsed_seconds: 0 } };
async function setup(page: Page, initial: Saved = { version: 0, workbook: null }, source = workbook) {
  let saved = initial;
  const writes: unknown[] = [];
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'flow'));
  await page.route('**/api/portfolio/**', r => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/session')) return r.fulfill({ json: {} });
    if (path.endsWith('/catalog')) return r.fulfill({ json: { instruments: [{ id: asset.instrument_id, name: asset.name, ticker: '069500', code: '069500', market: 'KR', kind: 'etf' }] } });
    if (path.endsWith('/tracking')) return r.fulfill({ json: { market: null, days: [] } });
    if (path.endsWith('/analyses/flow/workbook')) return r.fulfill({ json: source });
    if (path.endsWith('/workbook')) {
      if (r.request().method() === 'POST') { writes.push(r.request().postDataJSON()); saved = { version: saved.version + 1, workbook: r.request().postDataJSON().workbook }; }
      return r.fulfill({ json: saved });
    }
    if (path.endsWith('/analyses/flow') || path.endsWith('/analyses/latest')) return r.fulfill({ json: job });
    return r.fulfill({ json: {} });
  });
  return writes;
}

test('dashboard analysis reconciles tab-local pending input before restoring the latest report', async ({ page }) => {
  await setup(page, { version: 1, workbook });
  await page.addInitScript(() => sessionStorage.setItem('edge-portfolio-pending', JSON.stringify({
    input: JSON.stringify({ holdings: [], planning: true, horizon: '10년 이상' }), key: 'pending-in-dashboard'
  })));
  let submitted = false;
  await page.route('**/api/portfolio/analyses', route => {
    expect(route.request().headers()['idempotency-key']).toBe('pending-in-dashboard');
    submitted = true;
    return route.fulfill({ json: { id: 'recovered', state: 'queued' } });
  });
  await page.route('**/api/portfolio/analyses/recovered', route => route.fulfill({ json: { id: 'recovered', state: 'queued' } }));
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: 'AI 포폴 분석', exact: true }).click();
  await expect.poll(() => submitted).toBe(true);
  await expect(page.locator('.progress')).toBeVisible();
  await expect(page.locator('#result')).toHaveCount(0);
});

test('pending quotes appear promptly and polling slows down after they arrive', async ({ page }) => {
  await setup(page, { version: 1, workbook });
  let calls = 0;
  await page.route('**/api/portfolio/workbook/tracking', r => {
    calls++;
    return r.fulfill({ json: { market: calls === 1 ? null : { version: 1, payload: { date: '2026-10-19', assets: [{ id: 'holding', value: 1100000, profit: null, price: 11000, quantity: 100, price_date: '2026-10-19' }], missing: [], dividend_missing: ['holding'], forecasts: [], fx: null } }, days: [] } });
  });
  await page.clock.install();
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByRole('status').filter({ hasText: '시세' })).toBeVisible();
  await page.clock.runFor(3100);
  await expect(page.locator('.dashboard-stats')).toContainText('1,100,000원');
  await expect(page.getByRole('status').filter({ hasText: '시세' })).toHaveCount(0);
  expect(calls).toBe(2);
  await page.clock.runFor(59000);
  expect(calls).toBe(2);
  await page.clock.runFor(1100);
  await expect.poll(() => calls).toBe(3);
});

test('an empty portfolio does not enter fast quote polling', async ({ page }) => {
  await setup(page);
  let calls = 0;
  await page.route('**/api/portfolio/workbook/tracking', r => { calls++; return r.fulfill({ json: { market: null, days: [] } }); });
  await page.clock.install();
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByRole('button', { name: '종목 추가', exact: true })).toBeVisible();
  await expect.poll(() => calls).toBe(1);
  await page.clock.runFor(10000);
  expect(calls).toBe(1);
});

test('a failed workbook read does not block the independent saved analysis', async ({ page }) => {
  await setup(page);
  await page.route('**/api/portfolio/workbook', r => r.fulfill({ status: 503, json: {} }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByRole('button', { name: 'AI 포폴 분석', exact: true }).click();
  await expect(page.locator('#result')).toContainText('내 분석 결과');
});

test('catalog failure can be retried from the backtest without opening a holding editor', async ({ page }) => {
  await setup(page, { version: 1, workbook });
  await page.route('**/api/portfolio/workbook/catalog', r => r.fulfill({ status: 503, json: {} }));
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await expect(page.getByRole('button', { name: '목록 다시 불러오기' })).toBeVisible();
  await page.unroute('**/api/portfolio/workbook/catalog');
  await page.getByRole('button', { name: '목록 다시 불러오기' }).click();
  await page.getByRole('combobox', { name: /백테스트에 담은 종목/ }).fill('069500');
  await expect(page.getByRole('option', { name: /KODEX 200/ })).toBeVisible();
});

test('dashboard read has a motion state and remains usable after a failed retry', async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/portfolio/workbook', async r => { await held; await r.fulfill({ status: 503, json: {} }); });
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.ds-loading')).toBeVisible();
  expect(await page.locator('.dashboard-nav nav button').evaluateAll(buttons => buttons.every(button => {
    const rect = button.getBoundingClientRect();
    return rect.left >= 0 && rect.right <= innerWidth;
  }))).toBe(true);
  await expect(page.locator('.ds-loading .ds-spinner')).toHaveCSS('animation-name', 'none');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect(page.locator('.ds-loading .ds-spinner')).toHaveCSS('animation-name', 'ds-spin');
  await page.screenshot({ path: test.info().outputPath('dashboard-loading.png') });
  release();
  await expect(page.getByRole('button', { name: '저장된 내용 다시 불러오기' })).toBeEnabled();
});

test('another tab updating holdings offers reload instead of endless quote preparation', async ({ page }) => {
  await setup(page, { version: 1, workbook });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { market: { version: 2, payload: { date: '2026-10-19', assets: [], missing: [], dividend_missing: [], forecasts: [], fx: null } }, days: [] } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByRole('button', { name: '최신 보유 목록 불러오기' })).toBeVisible();
});

for (const tab of ['AI 포폴 분석', '배당 달력', '수익 기록', '백테스트']) test(`refresh preserves the selected screen: ${tab}`, async ({ page }) => {
  await setup(page, { version: 1, workbook });
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: tab, exact: true }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: tab, exact: true })).toHaveAttribute('aria-current', 'page');
  if (tab === 'AI 포폴 분석') await expect(page.locator('#result')).toContainText('내 분석 결과');
});

for (const status of [401, 503]) test(`session failure can be retried without losing the saved holdings: ${status}`, async ({ page }) => {
  await setup(page, { version: 1, workbook });
  await page.route('**/api/portfolio/session', r => r.fulfill({ status, json: {} }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByRole('alert')).toBeVisible();
  await page.unroute('**/api/portfolio/session');
  await page.getByRole('button', { name: '저장된 내용 다시 불러오기' }).click();
  await expect(page.locator('.holding-edit')).toContainText('KODEX 200');
});

test('tracking failure keeps holdings editable and provides a working retry', async ({ page }) => {
  await setup(page, { version: 1, workbook });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ status: 503, json: {} }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.holding-edit')).toBeEnabled();
  await expect(page.getByRole('alert')).toContainText('자동 기록');
  await page.unroute('**/api/portfolio/workbook/tracking');
  await page.getByRole('button', { name: '다시 확인', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('latest analysis failure can recover independently of the workbook', async ({ page }) => {
  await setup(page, { version: 1, workbook });
  await page.route('**/api/portfolio/analyses/latest', r => r.fulfill({ status: 503, json: {} }));
  await page.goto('/portfolio/?view=dashboard&tab=analysis');
  await expect(page.getByRole('button', { name: '다시 불러오기', exact: true })).toBeVisible();
  await page.unroute('**/api/portfolio/analyses/latest');
  await page.getByRole('button', { name: '다시 불러오기', exact: true }).click();
  await expect(page.locator('#result')).toContainText('내 분석 결과');
});

test('empty dashboard offers addition without pretending to collect nonexistent quotes', async ({ page }) => {
  await setup(page);
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByRole('button', { name: '종목 추가', exact: true })).toBeEnabled();
  await expect(page.locator('.ds-loading')).toHaveCount(0);
});
