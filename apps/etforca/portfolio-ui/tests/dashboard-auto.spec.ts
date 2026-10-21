import { test, expect, type Page } from '@playwright/test';
import { needsSeed, withSeed, profitSummary, type Asset, type Saved, type Workbook } from '../src/workbook';

const asset: Asset = { id: 'a', instrument_id: 'KR:069500', name: 'KODEX 200', account: 'ISA', category: '주식', currency: 'KRW', quantity: 10, average: 40000, price: null, cost_fx: null, amount: null, weight: null, target: null, fee: 0, price_date: null };
const workbook: Workbook = { title: '내 포트폴리오', fx: null, assets: [asset], dividends: [], months: [] };
const catalog = { instruments: [{ id: 'KR:069500', name: 'KODEX 200', ticker: '069500', code: '069500', market: 'KR', kind: 'etf' }] };
const tracking = { market: { version: 1, payload: { date: '2026-10-18', bases: { a: { signature: ['KR:069500', 10, null, null] } }, assets: [{ id: 'a', value: 500000, profit: 100000, price: 50000, price_date: '2026-10-16', quantity: 10 }], fx: null, forecasts: [{ asset_id: 'a', month: '2027-01', amount: 1000 }], missing: [], dividend_missing: [] } }, days: [] };

test('one missing purchase basis cannot hide known gains or dilute their return', () => {
  const q = tracking.market.payload.assets[0];
  expect(profitSummary([q, { ...q, id: 'missing', profit: null, value: 5000000 }])).toEqual({ profit: 100000, returnPct: 25, missing: 1 });
  expect(profitSummary([{ ...q, profit: null }])).toEqual({ profit: null, returnPct: null, missing: 1 });
});

test('partial daily profit stays visible and an imported QLD average survives saving without quantity', async ({ page }) => {
  const qld: Asset = { ...asset, id: 'qld', instrument_id: 'US:QLD', name: 'QLD', currency: 'USD', quantity: null, average: null, amount: 5000000 };
  await mock(page, { version: 1, workbook: { ...workbook, assets: [asset, qld] } });
  const quotes = [...tracking.market.payload.assets, { id: 'qld', value: 5000000, profit: null, price: 87.72, average: null, quantity: 42.5, price_date: '2026-10-16' }];
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { market: { version: 1, payload: { ...tracking.market.payload, assets: quotes } }, days: [{ date: '2026-10-19', value: 5500000, assets: quotes, fx: 1340 }] } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.dashboard-stats')).toContainText('확인된 평가손익');
  await expect(page.locator('.dashboard-stats')).toContainText('+100,000원');
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  await expect(page.locator('.return-records')).toContainText('확인된 평가손익 +100,000원 (+25.00%)');
  await expect(page.locator('.return-records')).toContainText('손익 미확인 1종목 제외');
  await page.screenshot({ path: `test-results/confirmed-profit-${test.info().project.name}.png`, fullPage: true });
  await page.getByRole('button', { name: '한눈에', exact: true }).click();
  await page.getByRole('button', { name: '평균매수가 입력', exact: true }).click();
  await page.getByLabel(/평균매수가/).fill('80');
  const saved = page.waitForRequest(r => r.url().endsWith('/workbook') && r.method() === 'POST');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  expect((await saved).postDataJSON().workbook.assets.find((a: Asset) => a.id === 'qld')).toMatchObject({ average: 80, quantity: null, amount: 5000000 });
  await page.reload();
  await page.locator('.holding-edit').filter({ hasText: 'QLD' }).click();
  await expect(page.getByLabel(/평균매수가/)).toHaveValue('80');
});

for (const amount of [500000, 0]) test(`copying holdings excludes zero balances instead of producing invalid allocations: ${amount}`, async ({ page }) => {
  const cash: Asset = { ...asset, id: 'cash', instrument_id: 'CASH:KRW', name: '현금', category: '현금', quantity: null, amount: 0 };
  await mock(page, { version: 1, workbook: { ...workbook, assets: [asset, cash] } });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [{ ...tracking.market.payload.assets[0], value: amount }, { id: 'cash', value: 0, profit: 0 }] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.holding-edit')).toHaveCount(2);
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('button', { name: '내 보유 종목으로 시작' }).click();
  await expect(page.locator('.backtest-rows input')).toHaveCount(amount ? 1 : 0);
  if (amount) await expect(page.getByLabel('KODEX 200 비중 (%)')).toHaveValue('100');
  else await expect(page.getByRole('alert')).toContainText('보유금액이 있는 종목을 먼저 담아 주세요.');
});
async function mock(page: Page, saved: Saved = { version: 1, workbook }) {
  await page.route('**/api/portfolio/**', async r => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/session')) return r.fulfill({ json: {} });
    if (path.endsWith('/catalog')) return r.fulfill({ json: catalog });
    if (path.endsWith('/tracking')) return r.fulfill({ json: tracking });
    if (path.endsWith('/workbook')) { if (r.request().method() === 'POST') saved = { version: saved.version + 1, workbook: r.request().postDataJSON().workbook }; return r.fulfill({ json: saved }); }
    return r.fulfill({ status: 404, json: {} });
  });
}

for (const stock of [
  { id: 'KR:005930', name: '삼성전자', ticker: '005930', market: 'KR', symbol: '005930.KS' },
  { id: 'KR:247540', name: '에코프로비엠', ticker: '247540', market: 'KR', symbol: '247540.KQ' },
  { id: 'US:AAPL', name: 'Apple', ticker: 'AAPL', market: 'US', symbol: 'AAPL' },
  { id: 'US:BRK.B', name: 'Berkshire Hathaway', ticker: 'BRK.B', market: 'US', symbol: 'BRK-B' },
]) test(`stock entry preserves identity, quantity and purchase basis on reload: ${stock.id}`, async ({ page }) => {
  await mock(page);
  await page.route('**/api/portfolio/workbook/catalog', r => r.fulfill({ json: { instruments: [...catalog.instruments, { ...stock, code: stock.ticker, kind: 'stock' }] } }));
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('combobox', { name: '계좌', exact: true }).selectOption('일반 계좌');
  await dialog.getByRole('combobox', { name: /종목 찾기/ }).fill(stock.symbol);
  await expect(page.getByRole('option', { name: new RegExp(stock.name) })).toContainText('상장 주식');
  await page.getByRole('option', { name: new RegExp(stock.name) }).click();
  await expect(dialog.getByLabel('보유 수량', { exact: true })).toBeFocused();
  await dialog.getByLabel('보유 수량', { exact: true }).fill('2.5');
  await dialog.getByLabel(/평균매수가/).fill('100');
  await page.screenshot({ path: `test-results/stock-entry-${stock.ticker}-${test.info().project.name}.png`, fullPage: true });
  const request = page.waitForRequest(r => r.url().endsWith('/workbook') && r.method() === 'POST');
  await dialog.getByRole('button', { name: '저장', exact: true }).click();
  expect((await request).postDataJSON().workbook.assets[1]).toMatchObject({ instrument_id: stock.id, quantity: 2.5, average: 100, category: '주식', currency: stock.market === 'US' ? 'USD' : 'KRW' });
  await expect(dialog).not.toBeVisible();
  await page.reload();
  await page.locator('.holding-edit').filter({ hasText: stock.name }).click();
  await expect(dialog.getByLabel('보유 수량', { exact: true })).toHaveValue('2.5');
  await expect(dialog.getByLabel(/평균매수가/)).toHaveValue('100');
  await dialog.getByRole('button', { name: '취소', exact: true }).click();
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('combobox', { name: /백테스트에 담은 종목 찾기/ }).fill(stock.ticker);
  await page.getByRole('option', { name: new RegExp(stock.name) }).click();
  await expect(page.getByLabel(`${stock.name} 비중 (%)`)).toBeVisible();
});

test('tiny positive holdings stay represented by a valid hundredth-percent allocation', async ({ page }) => {
  const cash: Asset = { ...asset, id: 'cash', instrument_id: 'CASH:KRW', name: '현금', category: '현금', quantity: null, amount: 100000000 };
  await mock(page, { version: 1, workbook: { ...workbook, assets: [asset, cash] } });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [{ ...tracking.market.payload.assets[0], value: 1 }, { id: 'cash', value: 100000000, profit: 0 }] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.holding-edit')).toHaveCount(2);
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('button', { name: '내 보유 종목으로 시작' }).click();
  await expect(page.getByLabel('KODEX 200 비중 (%)')).toHaveValue('0.01');
  await expect(page.getByLabel('현금 비중 (%)')).toHaveValue('99.99');
  await expect(page.getByRole('alert')).toContainText('0.01%보다 작은 보유분');
});

test('an unavailable first tracking response never presents the old import amount as current value', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, quantity: null, amount: 1000000 }] } });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ status: 503, json: {} }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByRole('alert')).toContainText('자동 기록을 불러오지 못했어요.');
  await expect(page.locator('.dashboard-stats article').first().locator('strong')).toHaveText('—');
  await expect(page.locator('.holdings-table tbody tr')).not.toContainText('1,000,000원');
});

for (const status of [401, 404]) test(`an expired backtest connection preserves the composition and permits a retry: ${status}`, async ({ page }) => {
  await mock(page);
  await page.route('**/api/portfolio/backtests', r => r.fulfill({ json: { id: 'job' } }));
  await page.route('**/api/portfolio/backtests/job', r => r.fulfill({ status, json: {} }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.holding-edit')).toHaveCount(1);
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('button', { name: '내 보유 종목으로 시작' }).click();
  await page.getByRole('button', { name: '과거 수익률 조회', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('백테스트 연결이 만료됐어요. 다시 조회해 주세요.');
  await expect(page.getByLabel('KODEX 200 비중 (%)')).toHaveValue('100');
  await expect(page.getByRole('button', { name: '과거 수익률 조회', exact: true })).toBeEnabled();
});

test('an older poll cannot replace the fresh valuation returned after saving', async ({ page }) => {
  await page.clock.install();
  await mock(page);
  let calls = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/portfolio/workbook/tracking', async r => {
    calls++;
    if (calls === 2) { await pending; return r.fulfill({ json: tracking }); }
    return r.fulfill({ json: calls > 2 ? { ...tracking, market: { ...tracking.market, version: 2, payload: { ...tracking.market.payload, assets: [{ ...tracking.market.payload.assets[0], value: 600000, profit: 200000 }] } } } : tracking });
  });
  await page.goto('/portfolio/?view=dashboard');
  const profit = page.locator('.dashboard-stats article').nth(1).locator('strong');
  await expect(profit).toHaveText('+100,000원 (+25.00%)');
  await page.clock.fastForward(60000);
  await expect.poll(() => calls).toBe(2);
  await page.locator('.holding-edit').first().click();
  await page.getByLabel('보유 수량', { exact: true }).fill('12');
  await page.getByRole('button', { name: '저장', exact: true }).click();
  await expect(profit).toHaveText('+200,000원 (+50.00%)');
  const oldResponse = page.waitForResponse('**/api/portfolio/workbook/tracking');
  release();
  await (await oldResponse).finished();
  await page.clock.runFor(100);
  await expect(profit).toHaveText('+200,000원 (+50.00%)');
});

test('an account-only edit does not rebase quantities or averages normalized by an earlier quote', async ({ page }) => {
  await mock(page);
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [{ ...tracking.market.payload.assets[0], quantity: 20, average: 20000 }] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.profit-percent')).toBeVisible();
  await page.locator('.holding-edit').first().click();
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('20');
  await expect(page.getByLabel(/평균매수가/)).toHaveValue('20000');
  await page.getByRole('dialog').getByRole('combobox', { name: '계좌', exact: true }).selectOption('IRP');
  const saving = page.waitForRequest(r => r.url().endsWith('/workbook') && r.method() === 'POST');
  await page.getByRole('button', { name: '저장', exact: true }).click();
  const savedAsset = (await saving).postDataJSON().workbook.assets[0];
  expect(savedAsset.account).toBe('IRP');
  expect(savedAsset.quantity).toBe(10);
  expect(savedAsset.average).toBe(40000);
  expect(savedAsset.quantity_revision).toBeUndefined();
  expect(savedAsset.average_revision).toBeUndefined();
});

test('holdings need only instrument quantity and optional average, never a customer quote or weight', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.locator('.holding-edit').first().click();
  for (const name of ['현재 비중 (%)', '현재가', '매입 환율 (USD)', '계좌 내 목표 비중 (%)', '가격 기준일']) await expect(page.getByLabel(name, { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('보유 수량', { exact: true })).toBeVisible();
  await expect(page.getByLabel(/평균매수가/)).toBeVisible();
  await expect(page.getByLabel(/평균매수가/)).not.toHaveAttribute('required', '');
});

test('the overview replaces redundant tabs with a working backtest', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.getByRole('navigation', { name: '대시보드' }).getByRole('button')).toHaveText(['한눈에', '배당 달력', '수익 기록', '백테스트', 'AI 포폴 분석']);
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('button', { name: '내 보유 종목으로 시작' }).click();
  await page.getByRole('button', { name: '과거 수익률 조회', exact: true }).click();
  await expect(page.getByRole('heading', { name: '기간 수익률 +34.5%' })).toBeVisible();
  await expect(page.locator('.history-legend')).toContainText('내 조합');
});

test('preview backtests and daily records make no customer requests', async ({ page }) => {
  const traffic: string[] = [];
  page.on('request', r => { if (/\/api\/portfolio|posthog\.com/.test(r.url())) traffic.push(r.url()); });
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  await expect(page.locator('.return-records article')).toHaveCount(3);
  await expect(page.locator('.dashboard-main form')).toHaveCount(1); // Only the hidden backtest form remains.
  await expect(page.getByRole('button', { name: '월 기록 저장' })).toHaveCount(0);
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('button', { name: '내 보유 종목으로 시작' }).click();
  await page.getByRole('button', { name: '과거 수익률 조회', exact: true }).click();
  await expect(page.getByRole('heading', { name: /기간 수익률/ })).toBeVisible();
  expect(traffic).toEqual([]);
});

test('forecast shows twelve months across January and scroll controls do not overflow the page', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  await expect(page.locator('.dashboard-card').filter({ has: page.getByRole('heading', { name: '배당 달력', exact: true }) }).locator('.dividend-bars button')).toHaveCount(13);
  const region = page.getByRole('region', { name: /12개월 배당 그래프/ }).first();
  await page.getByRole('button', { name: '다음 배당 월 보기' }).first().click();
  if (await region.evaluate(el => el.scrollWidth > el.clientWidth)) await expect.poll(() => region.evaluate(el => el.scrollLeft)).toBeGreaterThan(0);
  await page.getByRole('button', { name: /^2027-01 예상/ }).click();
  await expect(page.getByRole('heading', { name: /2027년 01월/ })).toBeVisible();
  await expect(page.locator('.dividend-records')).not.toBeEmpty();
  await expect(page.getByRole('button', { name: /^2027-10 예상/ })).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('a forecast never pretends to know a confirmed future payment day', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  await expect(page.locator('.dashboard-main')).toContainText('지급월과 금액은 실제 공시에 따라 달라질 수');
  await expect(page.locator('.dividend-calendar')).toHaveCount(0);
  await expect(page.locator('input[type=date]')).toHaveCount(2); // Backtest period, no invented dividend date.
});

test('seed conversion preserves total money even for tiny portfolios and leaves no negative remainder', () => {
  const w = { ...workbook, assets: Array.from({ length: 20 }, (_, i) => ({ ...asset, id: String(i), quantity: null, weight: 5 })) };
  expect(needsSeed(w)).toBe(true);
  const converted = withSeed(w, 1);
  expect(converted.assets.reduce((s, a) => s + Math.round(a.amount! * 100), 0)).toBe(100);
  expect(converted.assets.every(a => a.amount! >= 0 && a.weight === null)).toBe(true);
  expect(() => withSeed(w, NaN)).toThrow();
  expect(() => withSeed(w, 0)).toThrow();
  expect(() => withSeed({ ...w, assets: w.assets.slice(1) }, 100)).toThrow();
  const rounded = withSeed({ ...w, assets: [50.005, 50, 0].map((weight, i) => ({ ...asset, id: String(i), weight })) }, 1000000);
  expect(rounded.assets.every(a => a.amount! >= 0)).toBe(true);
  expect(rounded.assets.reduce((s, a) => s + a.amount!, 0)).toBe(1000000);
});

test('a percent-only dashboard asks for seed once and derives values and account weights', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, quantity: null, weight: 60 }, { ...asset, id: 'b', account: '일반 계좌', quantity: null, weight: 40 }] } });
  await page.goto('/portfolio/?view=dashboard');
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { market: null, days: [] } }));
  await page.reload();
  await page.getByLabel('총 투자금액 (원)').fill('1000000');
  await page.getByRole('button', { name: '금액 적용하고 저장' }).click();
  await expect(page.getByLabel('총 투자금액 (원)')).toHaveCount(0);
  await expect(page.locator('.holdings-table tbody tr').first()).toContainText('600,000원');
  await page.getByRole('combobox', { name: '계좌', exact: true }).selectOption('ISA');
  await expect(page.locator('.holdings-table tbody tr')).toContainText('100.0%');
});

test('saving a percent analysis pauses for seed before navigating or writing', async ({ page }) => {
  let writes = 0;
  let version = 0;
  await mock(page);
  await page.route('**/api/portfolio/workbook', r => { if (r.request().method() === 'POST') { writes++; expect(r.request().postDataJSON().workbook.assets[0].amount).toBe(1000000); if (writes === 1) { version = 2; return r.fulfill({ status: 409, json: { detail: 'WORKBOOK_CONFLICT' } }); } expect(r.request().postDataJSON().version).toBe(2); return r.fulfill({ json: {} }); } return r.fulfill({ json: { version, workbook: version ? workbook : null } }); });
  await page.route('**/api/portfolio/analyses/*/workbook', r => r.fulfill({ json: { ...workbook, assets: [{ ...asset, quantity: null, weight: 100 }] } }));
  await page.goto('/portfolio/?preview=result');
  // The real SavePortfolio component is exercised with fetch through the normal report route below.
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'seed-test'));
  await page.route('**/api/portfolio/analyses/seed-test', r => r.fulfill({ json: { id: 'seed-test', state: 'succeeded', result: { sections: { question: { paragraphs: [], evidence: [], limitations: [] }, portfolio: { paragraphs: [], evidence: [], limitations: [] }, outlook: { paragraphs: [], evidence: [], limitations: [] }, opinion: { paragraphs: [], evidence: [], limitations: [] } }, metrics: { holdings: [], total_krw: null, unavailable_reason: '' }, sources: [], as_of: '2026-10-18', model: 'test', elapsed_seconds: 0 } } }));
  await page.goto('/portfolio/');
  await page.locator('.feedback-anchor .save-portfolio button').first().click();
  expect(writes).toBe(0);
  await page.locator('.feedback-anchor').getByLabel('총 투자금액 (원)').fill('1000000');
  await page.locator('.feedback-anchor').getByRole('button', { name: '금액 적용하고 저장' }).click();
  await expect(page.locator('.feedback-anchor').getByRole('alert')).toContainText('다른 탭');
  await expect(page.locator('.feedback-anchor').getByLabel('총 투자금액 (원)')).toHaveValue('1000000');
  await page.locator('.feedback-anchor').getByRole('button', { name: '금액 적용하고 저장' }).click();
  await expect(page).toHaveURL(/view=dashboard/);
  expect(writes).toBe(2);
});

test('a pending save keeps the edited holding locked until the response arrives', async ({ page }) => {
  await mock(page);
  let release: (() => void) | undefined;
  await page.route('**/api/portfolio/workbook', async r => { if (r.request().method() === 'POST') { await new Promise<void>(resolve => { release = resolve; }); return r.fulfill({ json: { version: 2, workbook: r.request().postDataJSON().workbook } }); } return r.fulfill({ json: { version: 1, workbook } }); });
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.holding-edit').click();
  await page.getByLabel('보유 수량', { exact: true }).fill('11');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect.poll(() => !!release).toBe(true);
  await expect(page.getByLabel('보유 수량', { exact: true })).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeVisible();
  release!();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.holding-edit')).toBeFocused();
  await page.locator('.holding-edit').click();
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('11');
});

test('failed conflict recovery preserves the unsaved quantity and its discard protection', async ({ page }) => {
  await mock(page);
  let failReads = false;
  await page.route('**/api/portfolio/workbook', r => r.request().method() === 'POST' ? r.fulfill({ status: 409, json: { detail: 'WORKBOOK_CONFLICT' } }) : failReads ? r.fulfill({ status: 503, json: {} }) : r.fulfill({ json: { version: 1, workbook } }));
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.holding-edit').click();
  await page.getByLabel('보유 수량', { exact: true }).fill('123');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('다른 탭');
  failReads = true;
  await page.getByRole('dialog').getByRole('button', { name: '저장된 내용 다시 불러오기' }).click();
  await page.getByRole('button', { name: '다시 불러오기', exact: true }).click();
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('123');
  await page.keyboard.press('Escape');
  await expect(page.locator('.ds-confirm-dialog')).toContainText('저장하지 않은 수정');
});

test('invalid quantity cannot be saved and a missing average does not block holdings', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.locator('.holding-edit').filter({ hasText: '미국시장 ETF 예시' }).click();
  await page.getByLabel('보유 수량', { exact: true }).fill('-1');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByLabel('보유 수량', { exact: true }).fill('5');
  await page.getByLabel(/평균매수가/).fill('');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.holdings-table tbody tr').filter({ hasText: '미국시장 ETF 예시' })).toContainText('86,000원');
});

test('cash can be added without quantity average price or currency fields', async ({ page }) => {
  await page.goto('/portfolio/?preview=empty');
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  await page.getByRole('dialog').getByRole('combobox', { name: /종목 찾기/ }).fill('현금');
  await page.getByRole('option', { name: /현금/ }).click();
  await page.getByLabel('현금 (원)', { exact: true }).fill('123456');
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveCount(0);
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.locator('.dashboard-stats')).toContainText('123,456원');
  await expect(page.getByRole('button', { name: '종목 추가', exact: true })).toBeFocused();
});

test('backtest edits stay available when navigating to another dashboard tab', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('combobox', { name: '백테스트에 담은 종목 찾기' }).fill('VOO');
  await page.getByRole('option', { name: /Vanguard/ }).click();
  await page.getByLabel('시작일 · 선택').fill('2020-01-01');
  await page.getByRole('button', { name: '한눈에', exact: true }).click();
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await expect(page.getByLabel('시작일 · 선택')).toHaveValue('2020-01-01');
  await expect(page.getByLabel(/Vanguard.*비중/)).toHaveValue('100');
});

test('unknown legacy instruments require a real catalog match instead of fetching guessed symbols', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, instrument_id: null, name: '내가 적은 별명' }] } });
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.holding-edit').click();
  await expect(page.getByRole('dialog')).toContainText('자동 시세를 연결할 종목을 선택');
  await expect(page.getByRole('dialog').getByRole('button', { name: '저장', exact: true })).toBeDisabled();
  await page.getByRole('dialog').getByRole('combobox', { name: /종목 찾기/ }).fill('069500');
  await page.getByRole('option', { name: /KODEX/ }).click();
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('10');
  await expect(page.getByLabel(/평균매수가/)).toHaveValue('40000');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.locator('.holdings-table tbody tr')).toContainText('KODEX 200');
});

test('linking an old amount-only holding keeps its amount without demanding quantity', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, instrument_id: null, quantity: null, amount: 123456 }] } });
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.holding-edit').click();
  await page.getByRole('dialog').getByRole('combobox', { name: /종목 찾기/ }).fill('069500');
  await page.getByRole('option', { name: /KODEX/ }).click();
  await expect(page.getByLabel('가져온 보유금액 (원)')).toHaveValue('123456');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.locator('.holding-edit').click();
  await expect(page.getByLabel('가져온 보유금액 (원)')).toHaveValue('123456');
});

test('an unavailable forecast is never shown as a confirmed zero in the calendar', async ({ page }) => {
  await mock(page);
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, forecasts: [], dividend_missing: ['a'] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  await expect(page.getByRole('heading', { name: /예상 미확인/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /예상 0원/ })).toHaveCount(0);
  await expect(page.locator('.dashboard-main')).toContainText('배당 자료를 확인하지 못한 종목');
});

test('a quote outage cannot restore the original import amount as a current valuation', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, quantity: null, amount: 1000000 }] } });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [], missing: ['a'] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.notice')).toContainText('일부 종목의 시세');
  await expect(page.locator('.holdings-table tbody tr')).not.toContainText('1,000,000원');
  await expect(page.locator('.dashboard-stats article').first().locator('strong')).toHaveText('—');
});

test('historical dividend records remain reachable outside the future calendar window', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, dividends: [{ id: 'old', asset_id: 'a', date: '2025-01-15', status: '실수령', amount: 1234 }] } });
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  await page.getByText('저장된 지급 내역', { exact: true }).click();
  await expect(page.locator('.dashboard-main details')).toContainText('2025-01-15 · KODEX 200 · 실수령 1,234원');
});

test('removing a holding retains manual dividend records with its original name', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.locator('.holding-edit').filter({ hasText: '미국시장 ETF 예시' }).click();
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await page.locator('.holding-edit').filter({ hasText: '미국시장 ETF 예시' }).click();
  await page.getByRole('button', { name: '종목 삭제', exact: true }).click();
  await page.getByRole('button', { name: '삭제하기', exact: true }).click();
  await expect(page.getByRole('button', { name: '종목 추가', exact: true })).toBeFocused();
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  await expect(page.getByText('보관된 이전 배당·월 기록', { exact: true })).toHaveCount(0);
  const archives = await page.evaluate(() => JSON.parse(localStorage.getItem('edge-preview-workbook')!).archives);
  expect(JSON.stringify(archives)).toContain('미국시장 ETF 예시');
  expect(JSON.stringify(archives)).toContain('31000');
});

test('the AI tab shows the original report and can start another analysis without losing tab drafts', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: 'AI 포폴 분석', exact: true }).click();
  await expect(page.locator('#report')).toBeVisible();
  await expect(page.locator('.report-history')).toBeVisible();
  await page.getByRole('button', { name: '새로 분석하기', exact: true }).click();
  await expect(page.locator('.step').first()).toBeVisible();
  await expect(page.locator('#report')).toHaveCount(0);
  await page.getByRole('button', { name: '한눈에', exact: true }).click();
  await page.getByRole('button', { name: 'AI 포폴 분석', exact: true }).click();
  await expect(page.locator('.step').first()).toBeVisible();
});

test('latest analysis is recovered from the server even without the local result pointer', async ({ page }) => {
  await mock(page);
  const job = { id: 'server-latest', state: 'running', stage: 'portfolio', result: null, started_at: new Date().toISOString() };
  await page.route('**/api/portfolio/analyses/*', r => r.fulfill({ json: job }));
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: 'AI 포폴 분석', exact: true }).click();
  await expect(page.locator('.research-motion svg')).toBeVisible();
  await expect(page.locator('.progress-steps')).toHaveCount(0);
  await expect(page.locator('.research-motion g.active')).toHaveCount(1);
  await expect(page.getByRole('list', { name: '분석 역할과 검토 흐름' })).toBeVisible();
});

for (const [profit, value, expected] of [[100000, 500000, '+100,000원 (+25.00%)'], [-100000, 300000, '-100,000원 (-25.00%)'], [0, 400000, '0원 (0.00%)'], [null, 400000, '—'], [100000, 100000, '+100,000원']] as const) {
  test(`profit percentage uses cost basis and never invents missing or zero-cost returns: ${expected}`, async ({ page }) => {
    await mock(page);
    await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [{ ...tracking.market.payload.assets[0], profit, value }] } } } }));
    await page.goto('/portfolio/?view=dashboard');
    await expect(page.locator('.dashboard-stats article').nth(1).locator('strong')).toHaveText(expected);
  });
}

test('aggregate return excludes cash and follows the selected account', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [asset, { ...asset, id: 'b', account: 'IRP' }, { ...asset, id: 'cash', category: '현금', amount: 9000000, quantity: null }] } });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [...tracking.market.payload.assets, { id: 'b', value: 150000, profit: -50000 }, { id: 'cash', value: 9000000, profit: 0 }] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  const result = page.locator('.dashboard-stats article').nth(1).locator('strong');
  await expect(result).toHaveText('+50,000원 (+8.33%)');
  await page.getByRole('combobox', { name: '계좌', exact: true }).selectOption('IRP');
  await expect(result).toHaveText('-50,000원 (-25.00%)');
});

test('mixed legacy weights and known cash remain editable without an impossible seed conversion', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, quantity: null, weight: 50 }, { ...asset, id: 'cash', instrument_id: 'CASH:KRW', name: '현금', category: '현금', quantity: null, amount: 100000 }] } });
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByLabel('총 투자금액 (원)')).toHaveCount(0);
  await page.locator('.holding-edit').first().click();
  await page.getByLabel('보유 수량', { exact: true }).fill('10');
  const write = page.waitForRequest(r => r.url().endsWith('/workbook') && r.method() === 'POST');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  const updated = (await write).postDataJSON().workbook.assets;
  expect(updated[0].quantity).toBe(10);
  expect(updated[0].weight).toBeNull();
  expect(updated[1].amount).toBe(100000);
});

test('a temporary polling outage resumes the accepted backtest without submitting another job', async ({ page }) => {
  await mock(page);
  let submits = 0, polls = 0;
  await page.route('**/api/portfolio/backtests', r => { submits++; return r.fulfill({ json: { id: 'retained' } }); });
  await page.route('**/api/portfolio/backtests/retained', r => ++polls === 1 ? r.fulfill({ status: 503, json: {} }) : r.fulfill({ json: { state: 'succeeded', result: { return_pct: 25, excluded: [], history: { points: [], currency: 'KRW', basis: '', method: '', source_url: '' } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('button', { name: '내 보유 종목으로 시작' }).click();
  await page.getByRole('button', { name: '과거 수익률 조회', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('조회 연결을 다시 시도');
  await expect(page.getByRole('button', { name: '내 보유 종목으로 시작' })).toBeDisabled();
  await expect(page.getByRole('heading', { name: '기간 수익률 +25%' })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(submits).toBe(1);
});


test('legacy percentages below 100 remain editable instead of trapping the owner in seed conversion', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, quantity: null, weight: 60 }] } });
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByLabel('총 투자금액 (원)')).toHaveCount(0);
  await page.locator('.holding-edit').click();
  await page.getByLabel('보유 수량', { exact: true }).fill('10');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});


test('recovering the first workbook read also restores ETF search', async ({ page }) => {
  await mock(page);
  let fail = true;
  await page.route('**/api/portfolio/workbook', r => fail ? r.fulfill({ status: 503, json: {} }) : r.fulfill({ json: { version: 1, workbook } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.getByRole('button', { name: '저장된 내용 다시 불러오기' })).toBeVisible();
  fail = false;
  await page.getByRole('button', { name: '저장된 내용 다시 불러오기' }).click();
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  await page.getByRole('dialog').getByRole('combobox', { name: /종목 찾기/ }).fill('069500');
  await expect(page.getByRole('option', { name: /KODEX/ })).toBeVisible();
});


test('saving keeps the last valuation instead of blanking it or restoring the old import amount', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, quantity: null, amount: 1000000 }] } });
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.holdings-table tbody tr')).toContainText('500,000원');
  await page.locator('.holding-edit').click();
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.locator('.dashboard-stats article').first().locator('strong')).toHaveText('500,000원');
  await expect(page.locator('.dashboard-stats')).toContainText('갱신 중 · 이전 값');
  await expect(page.locator('.holdings-table tbody tr')).not.toContainText('1,000,000원');
});

test('new holdings do not clear existing quotes and stale quotes never overwrite edited inputs', async ({ page }) => {
  await mock(page);
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.holding-edit').click();
  await page.getByLabel('보유 수량', { exact: true }).fill('12');
  await page.getByLabel(/평균매수가/).fill('42000');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.locator('.dashboard-stats')).toContainText('500,000원');
  await expect(page.locator('.dashboard-stats')).toContainText('+100,000원');
  await expect(page.locator('.allocation-donut')).toBeVisible();
  await page.locator('.holding-edit').click();
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('12');
  await expect(page.getByLabel(/평균매수가/)).toHaveValue('42000');
  await page.getByRole('button', { name: '수정 닫기', exact: true }).click();
  const added = { ...asset, id: 'new', instrument_id: 'US:QLD', name: 'QLD', currency: 'USD' as const };
  await page.route('**/api/portfolio/workbook', r => r.fulfill({ json: { version: 3, workbook: { ...workbook, assets: [asset, added] } } }));
  await page.reload();
  await expect(page.locator('.dashboard-stats article').first()).toContainText('확인된 자산500,000원');
  await expect(page.locator('.holding-edit')).toHaveCount(2);
  await expect(page.locator('.holdings-table tbody tr').filter({ hasText: 'KODEX' })).toContainText('500,000원');
  await page.screenshot({ path: `test-results/quote-refresh-${test.info().project.name}.png`, fullPage: true });
});

test('a different instrument with the same row id cannot inherit the previous quote', async ({ page }) => {
  await mock(page, { version: 2, workbook: { ...workbook, assets: [{ ...asset, instrument_id: 'US:QLD', name: 'QLD', currency: 'USD' }] } });
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.holding-edit')).toContainText('QLD');
  await expect(page.locator('.holdings-table')).not.toContainText('500,000원');
});

test('cash stays visible when another holding is saved even without a split basis', async ({ page }) => {
  const cash: Asset = { ...asset, id: 'cash', instrument_id: 'CASH:KRW', name: '현금', category: '현금', quantity: null, amount: 200000 };
  await mock(page, { version: 2, workbook: { ...workbook, assets: [asset, cash] } });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [...tracking.market.payload.assets, { id: 'cash', value: 200000, profit: null, price: null, quantity: null, price_date: null }] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.dashboard-stats article').first()).toContainText('700,000원');
});

test('retained USD prices use the exchange rate of their valuation when FX refreshes', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, instrument_id: 'US:QLD', name: 'QLD', currency: 'USD', average: 80 }] } });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { version: 1, payload: { ...tracking.market.payload, fx: 1400, missing: ['a'], assets: [{ id: 'a', instrument_id: 'US:QLD', quantity: 10, price: 100, average: 80, value: 1300000, profit: 260000 }] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  if (test.info().project.name === 'mobile') await page.getByRole('button', { name: 'QLD 상세 정보 보기' }).click();
  const row = page.locator('.holdings-table tbody tr');
  await expect(row.locator('[data-label="현재가"]')).toContainText('130,000원');
  await expect(row.locator('[data-label="평단가"]')).toContainText('104,000원');
  await expect(row.locator('[data-label="평가금액"]')).toContainText('1,300,000원');
});

test('an unknown value is not presented as a previous valuation during refresh', async ({ page }) => {
  await mock(page, { version: 1, workbook: { ...workbook, assets: [{ ...asset, quantity: null, amount: null }] } });
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [{ ...tracking.market.payload.assets[0], value: null, quantity: null, profit: null }], missing: ['a'] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.notice')).toContainText('일부 종목의 시세나 수량');
  await expect(page.locator('.dashboard-stats')).not.toContainText('갱신 중 · 이전 값');
});

test('a failed confirmed deletion returns focus to the enabled delete control', async ({ page }) => {
  await mock(page);
  await page.route('**/api/portfolio/workbook', r => r.request().method() === 'POST' ? r.fulfill({ status: 409, json: { detail: 'WORKBOOK_CONFLICT' } }) : r.fulfill({ json: { version: 1, workbook } }));
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.holding-edit').click();
  await page.getByRole('button', { name: '종목 삭제', exact: true }).click();
  await page.getByRole('button', { name: '삭제하기', exact: true }).click();
  await expect(page.getByRole('button', { name: '종목 삭제', exact: true })).toBeFocused();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('다른 탭');
});


test('correcting a split-adjusted average back to its old number records an explicit edit', async ({ page }) => {
  await mock(page);
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [{ ...tracking.market.payload.assets[0], average: 20000 }] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.holding-edit').click();
  await expect(page.getByLabel(/평균매수가/)).toHaveValue('20000');
  await page.getByLabel(/평균매수가/).fill('40000');
  const write = page.waitForRequest(r => r.url().endsWith('/workbook') && r.method() === 'POST');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  const changed = (await write).postDataJSON().workbook.assets[0];
  expect(changed.average).toBe(40000);
  expect(changed.average_revision).toBeTruthy();
});


test('a known split waiting for its first close never asks the owner to re-enter correct holdings', async ({ page }) => {
  await mock(page);
  await page.route('**/api/portfolio/workbook/tracking', r => r.fulfill({ json: { ...tracking, market: { ...tracking.market, payload: { ...tracking.market.payload, assets: [], missing: ['a'], pending_splits: ['a'] } } } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.notice')).toHaveText('분할된 종목은 분할 후 첫 종가를 확인하면 평가액을 표시해요.');
  await expect(page.locator('.dashboard-stats article').first().locator('strong')).toHaveText('—');
});
