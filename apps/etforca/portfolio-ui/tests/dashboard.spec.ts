import { test, expect } from '@playwright/test';
import { cost, performance, purchases, value, type Asset } from '../src/workbook';

const asset: Asset = { id: 'a', name: 'ETF', account: 'ISA', category: '주식', currency: 'USD', quantity: 80, average: 25, price: 28, cost_fx: 1300, amount: null, weight: null, target: 100, fee: 5000, price_date: null };
test('money calculations preserve FX, fees and unavailable cost; purchases never exceed the budget', () => {
  expect(value(asset, 1360)).toBe(3046400);
  expect(cost(asset)).toBe(2605000);
  expect(cost({ ...asset, cost_fx: null })).toBeNull();
  expect(value(asset, null)).toBeNull();
  const plan = purchases([asset], 1360, 1000000);
  expect(plan[0].quantity).toBe(26);
  expect(plan[0].amount).toBeLessThanOrEqual(1000000);
  expect(purchases([{ ...asset, amount: null, quantity: null }], 1360, 1000000)).toEqual([]);
  const balance = [{ ...asset, currency: 'KRW' as const, quantity: null, price: null, amount: 800, target: 80 }, { ...asset, id: 'cash', category: '현금', quantity: null, price: null, amount: 200, target: 20 }];
  expect(purchases(balance, null, 100)[0].amount).toBe(80);
  expect(purchases([{ ...balance[0], amount: 200, target: 20 }, { ...balance[1], amount: 800, target: null }], null, 100)[0].amount).toBe(20);
  const odd = purchases([{ ...balance[0], amount: 0, target: 50 }, { ...balance[0], id: 'other', amount: 0, target: 50 }], null, 1000001);
  expect(odd.reduce((s, p) => s + p.amount, 0)).toBe(1000000);
  expect(odd.every(p => Number.isInteger(p.amount))).toBe(true);
  const incomplete = [{ ...balance[0], amount: 1000000, target: 40 }, { ...balance[0], id: 'b', amount: 0, target: 40 }];
  const implicitCash = purchases(incomplete, null, 100000);
  const explicitCash = purchases([...incomplete, { ...balance[1], amount: 0, target: 20 }], null, 100000);
  expect(implicitCash.map(p => p.amount)).toEqual(explicitCash.map(p => p.amount));
  expect(implicitCash[1].amount).toBe(66666);
  const exactShare = purchases([{ ...asset, quantity: 0, price: 38.2 }], 1300, 49660);
  expect(exactShare[0].quantity).toBe(1);
  expect(exactShare[0].amount).toBe(49660);
});
test('a deposit is not investment profit and missing months do not compound across gaps', () => {
  const rows = performance([{ month: '2026-01', value: 100, deposit: 0, withdrawal: 0, return_pct: 10 }, { month: '2026-02', value: 200, deposit: 100, withdrawal: 0, return_pct: null }, { month: '2026-04', value: 220, deposit: 0, withdrawal: 0, return_pct: 5 }]);
  expect(rows[1].monthly).toBeNull();
  expect(rows[2].cumulative).toBeCloseTo(5);
});
test('preview edits and reloads without sending customer API or analytics traffic', async ({ page }) => {
  const calls: string[] = [];
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/portfolio') || new URL(r.url()).hostname.endsWith('posthog.com')) calls.push(r.url()); });
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.locator('.dashboard-stats')).toContainText('25,586,400원');
  await expect(page.locator('.dashboard-stats')).toContainText('1,673,400원');
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  await page.getByRole('dialog').press('Escape');
  await expect(page.getByRole('button', { name: '종목 추가', exact: true })).toBeFocused();
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  await page.getByLabel('종목명', { exact: true }).fill('새 ETF');
  await page.getByLabel('평가금액 (원)', { exact: true }).fill('100000');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '종목 추가', exact: true })).toBeFocused();
  await page.reload();
  await expect(page.locator('.asset-list')).toContainText('새 ETF');
  await expect(page.locator('.dashboard-stats')).toContainText('25,686,400원');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  await expect(page.getByRole('button', { name: '배당 기록', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  await expect(page.getByRole('button', { name: '월 기록 저장' })).toBeVisible();
  await page.getByRole('button', { name: '추가매수', exact: true }).click();
  await expect(page.locator('.dashboard-main')).toContainText('매수할 계좌를 선택');
  expect(calls).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test('result can save original holdings into the dashboard without analysis', async ({ page }) => {
  await page.goto('/portfolio/?preview=result');
  await page.locator('.feedback-anchor > .save-portfolio button').click();
  await expect(page).toHaveURL(/preview=dashboard/);
  await expect(page.locator('.asset-list')).toContainText('금 ETF 예시');
  await expect(page.locator('.dashboard-stats')).toContainText('25,586,400원');
});

test('preview navigation and repeated followups remain isolated from a saved customer job', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'customer-secret'));
  const calls: string[] = [];
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/portfolio')) calls.push(r.url()); });
  await page.goto('/portfolio/?preview=dashboard');
  await page.locator('.dashboard-brand').click();
  await expect(page).toHaveURL(/preview=input/);
  await page.goto('/portfolio/?preview=newsletter');
  await page.getByRole('link', { name: '포트폴리오로 돌아가기' }).click();
  await expect(page).toHaveURL(/preview=input/);
  await page.goto('/portfolio/?preview=result');
  for (const question of ['어떻게 살까요?', '한 번 더 설명해 주세요']) {
    await page.getByRole('textbox', { name: '추가 질문', exact: true }).fill(question);
    await page.getByRole('button', { name: '질문 보내기' }).click();
    await expect(page.locator('.chat-answer')).toContainText('테스트 답변');
    await expect(page.getByRole('button', { name: '질문 보내기' })).toBeEnabled();
  }
  expect(calls).toEqual([]);
  expect(await page.evaluate(() => localStorage.getItem('edge-portfolio-job'))).toBe('customer-secret');
});
for (const screen of ['input', 'queued', 'running', 'partial', 'failed', 'empty', 'newsletter']) {
  test(`deployed preview supports ${screen} without an agent or network writes`, async ({ page }) => {
    const calls: string[] = [];
    page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/portfolio') || new URL(r.url()).hostname.endsWith('posthog.com')) calls.push(r.url()); });
    await page.goto(`/portfolio/?preview=${screen}`);
    await expect(page.locator('.preview-bar')).toBeVisible();
    await expect(page.locator('#root')).not.toBeEmpty();
    await page.waitForTimeout(200);
    expect(calls).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('account filter uses the selected account denominator for percent-only imports', async ({ page }) => {
  await page.route('**/api/portfolio/session', route => route.fulfill({ json: {} }));
  await page.route('**/api/portfolio/workbook', route => route.fulfill({ json: { version: 1, workbook: { title: '내 포트폴리오', fx: null, assets: [{ ...asset, quantity: null, average: null, price: null, weight: 60 }, { ...asset, id: 'b', name: '다른 ETF', account: '일반 계좌', quantity: null, average: null, price: null, weight: 40 }], dividends: [], months: [] } } }));
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.asset-list')).toContainText('60% · 전체 기준');
  await page.locator('.dashboard-main header select').selectOption('ISA');
  await expect(page.locator('.asset-list')).toContainText('100.0%');
  await expect(page.locator('.asset-list')).not.toContainText('다른 ETF');
});

test('an in-flight save cannot discard a different assets draft', async ({ page }) => {
  const workbook = { title: '내 포트폴리오', fx: 1360, assets: [asset, { ...asset, id: 'b', name: '다른 ETF' }], dividends: [], months: [] };
  let release: (() => void) | undefined;
  await page.route('**/api/portfolio/session', route => route.fulfill({ json: {} }));
  await page.route('**/api/portfolio/workbook', async route => {
    if (route.request().method() === 'POST') {
      await new Promise<void>(resolve => { release = resolve; });
      return route.fulfill({ json: { version: 2, workbook: route.request().postDataJSON().workbook } });
    }
    return route.fulfill({ json: { version: 1, workbook } });
  });
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.asset-row').first().click();
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect.poll(() => !!release).toBe(true);
  await expect(page.getByLabel('종목명', { exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '수정 닫기' }).click();
  await expect(page.locator('.asset-row').nth(1)).toBeDisabled();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  release!();
  await expect(page.locator('.asset-row').nth(1)).toBeEnabled();
  await page.locator('.asset-row').nth(1).click();
  await expect(page.getByLabel('종목명', { exact: true })).toHaveValue('다른 ETF');
  await expect(page.locator('.dashboard-error')).toHaveCount(0);
});

test('a late initial read cannot roll back a newer successful save', async ({ page }) => {
  let release: (() => void) | undefined;
  let reads = 0;
  const workbook = { title: '내 포트폴리오', fx: 1360, assets: [asset], dividends: [], months: [] };
  await page.route('**/api/portfolio/session', route => route.fulfill({ json: {} }));
  await page.route('**/api/portfolio/workbook', async route => {
    if (route.request().method() === 'POST') return route.fulfill({ json: { version: 3, workbook: route.request().postDataJSON().workbook } });
    const first = ++reads === 1;
    if (first) await new Promise<void>(resolve => { release = resolve; });
    return route.fulfill({ json: { version: first ? 1 : 2, workbook } });
  });
  await page.goto('/portfolio/?view=dashboard');
  await page.locator('.asset-row').click();
  await page.getByLabel('종목명', { exact: true }).fill('최신 저장');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.locator('.asset-row')).toContainText('최신 저장');
  const late = page.waitForResponse(r => r.url().endsWith('/workbook') && r.request().method() === 'GET');
  release!();
  await late;
  await expect(page.locator('.asset-row')).toContainText('최신 저장');
});
