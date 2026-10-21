import { test, expect } from '@playwright/test';
import { classifyAsset } from '../src/assetClassification';
import type { Asset } from '../src/workbook';

for (const state of ['missing', 'zero', 'missing-cost', 'large'] as const) test(`partial and boundary holdings remain truthful: ${state}`, async ({ page }) => {
  const a: Asset = { id: 'a', instrument_id: 'US:SCHD', name: 'Schwab U.S. Dividend Equity ETF', account: '일반 계좌', category: '주식', currency: 'USD', quantity: 10, average: 25, price: null, cost_fx: null, amount: null, weight: null, target: null, fee: 0, price_date: null };
  const quantity = state === 'zero' ? 0 : state === 'large' ? 100000000 : 20;
  const value = quantity * 50 * 1400;
  await page.route('**/api/portfolio/**', r => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/workbook')) return r.fulfill({ json: { version: 1, workbook: { title: '내 포트폴리오', fx: null, assets: [a], months: [], dividends: [] } } });
    if (path.endsWith('/tracking')) return r.fulfill({ json: { market: { version: 1, payload: { date: '2026-10-18', assets: state === 'missing' ? [] : [{ id: 'a', quantity, average: state === 'missing-cost' ? null : 40, price: 50, price_date: '2026-10-16', value, profit: state === 'missing-cost' ? null : quantity * 10 * 1400, dividend_yield_pct: state === 'zero' ? 0 : null }], fx: state === 'missing' ? null : 1400, forecasts: [], missing: state === 'missing' ? ['a'] : [], dividend_missing: state === 'zero' ? [] : ['a'] } }, days: [] } });
    return r.fulfill({ json: path.endsWith('/catalog') ? { instruments: [] } : {} });
  });
  await page.goto('/portfolio/?view=dashboard');
  const row = page.locator('.holdings-table tbody tr');
  await expect(row).toBeVisible();
  if (state === 'missing') {
    for (const field of ['주식수', '평단가', '현재가', '배당률', '예상 연배당금']) await expect(row.locator(`[data-label="${field}"]`)).toHaveText('—');
    await expect(page.locator('.dividend-metrics')).not.toContainText('0.0%');
  } else if (state === 'zero') {
    await expect(row.locator('[data-label=평가금액]')).toHaveText('0원');
    await expect(row.locator('[data-label=수익률]')).toHaveText('—');
  } else if (state === 'missing-cost') {
    await expect(row.locator('[data-label=평단가]')).toHaveText('—');
    await expect(row.locator('[data-label=수익률]')).toHaveText('—');
  } else await expect(row.locator('[data-label=평가금액]')).toHaveText('7,000,000,000,000원');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

for (const [id, name, region, theme] of [
  ['KR:360750', 'TIGER 미국S&P500', '미국', '시장 대표'],
  ['KR:379810', 'KODEX 미국나스닥100', '미국', 'AI·기술'],
  ['KR:069500', 'KODEX 200', '국내', '시장 대표'],
  ['KR:152100', 'PLUS 200', '국내', '시장 대표'],
  ['US:EMXC', 'iShares MSCI Emerging Markets ex China ETF', '미분류', '미분류'],
  ['US:QYLD', 'Global X Nasdaq 100 Covered Call ETF', '미국', '커버드콜'],
  ['US:IBB', 'iShares Biotechnology ETF', '미분류', '헬스케어'],
  ['KR:203780', 'TIGER 미국나스닥바이오', '미국', '헬스케어'],
  ['US:QCLN', 'First Trust NASDAQ Clean Edge Green Energy ETF', '미국', '친환경·에너지'],
  ['US:GSLC', 'Goldman Sachs ActiveBeta U.S. Large Cap Equity ETF', '미국', '미분류'],
  ['US:VXUS', 'Vanguard Total International Stock ETF', '미국 외', '시장 대표'],
  ['US:SCHD', 'SCHD', '미국', '배당'], ['US:VT', 'VT', '글로벌', '시장 대표'],
  ['KR:999999', 'TIGER 중국반도체', '중국', '반도체'],
  ['KR:999998', 'ACE 인도NIFTY50', '인도', '미분류'],
  ['KR:999996', 'KODEX 인도네시아MSCI(합성)', '미분류', '미분류'],
  ['KR:999997', '일본 닛케이', '일본', '미분류'],
  ['US:TEST', 'Unknown ETF', '미분류', '미분류'],
  ['KR:132030', 'KODEX 골드선물', '글로벌', '금·원자재'],
  ['CASH:KRW', '현금', '현금', '현금'],
]) test(`exposure classification does not confuse listing/currency with underlying exposure: ${id}`, () => {
  expect(classifyAsset({ instrument_id: id, name, category: id.startsWith('CASH') ? '현금' : '기타' })).toEqual({ region, theme });
});

test('dividend summary uses cash sum and the holdings expose every requested field', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.getByRole('heading', { name: '배당 분석' })).toBeVisible();
  await expect(page.locator('.dividend-metrics')).toContainText('예상 연간');
  await expect(page.locator('.dividend-legend')).toContainText('재투자 없이 합산');
  const headers = await page.locator('.holdings-table thead th').allTextContents();
  for (const field of ['주식수', '평단가', '현재가', '평가금액', '수익률', '평가손익', '배당률', '예상 연배당금', '배당주기']) expect(headers).toContain(field);
  const us = page.locator('.holdings-table tbody tr').filter({ hasText: '배당성장 ETF 예시' });
  await expect(us.locator('[data-label=현재가]')).toContainText('$');
  await page.getByRole('button', { name: '투자 테마', exact: true }).click();
  await expect(page.getByRole('button', { name: '투자 테마', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.allocation-donut')).toHaveAttribute('aria-label', /현금/);
});

test('annual dividend matrix separates received records from estimates and survives account/year switches', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  const report = page.locator('.dividend-report');
  await expect(report.locator('thead th')).toHaveCount(14);
  await expect(report.locator('tbody tr')).toHaveCount(5);
  await expect(report.getByRole('button', { name: '2026-01 예상 미확인', exact: true })).toBeVisible();
  await report.getByRole('button', { name: '실수령 기록', exact: true }).click();
  await expect(report.locator('tfoot td').last()).toHaveText('85,400원');
  await report.getByLabel('연도').selectOption('2027');
  await expect(report.locator('tfoot td').last()).toHaveText('—');
  await expect(report.getByRole('button', { name: '2027-01 실수령 미기록', exact: true })).toBeVisible();
  await report.getByRole('button', { name: '예상 배당', exact: true }).click();
  await expect(report.locator('tfoot td').last()).not.toHaveText('—');
  await page.locator('.dashboard-main header select').selectOption('ISA');
  const names = await report.locator('tbody th').allTextContents();
  expect(names.every(n => n.includes('ISA'))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('daily records hide old manual sections and source disclosures without deleting stored history', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  await expect(page.getByRole('heading', { name: '일별 수익 기록' })).toBeVisible();
  await expect(page.locator('.return-records time')).toHaveText(['2026-10-18', '2026-10-17', '2026-10-16']);
  await expect(page.getByText('계산 기준과 출처', { exact: true })).toHaveCount(0);
  await expect(page.getByText('이전에 직접 저장한 월 기록', { exact: true })).toHaveCount(0);
});

test('the same instrument cannot accidentally be added twice to the same account', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('combobox', { name: /종목 찾기/ }).fill('069500');
  await dialog.getByRole('option', { name: /KODEX 200/ }).click();
  await dialog.getByLabel('보유 수량', { exact: true }).fill('10');
  await dialog.getByRole('button', { name: '저장', exact: true }).click();
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  await dialog.getByRole('combobox', { name: /종목 찾기/ }).fill('069500');
  await dialog.getByRole('option', { name: /KODEX 200/ }).click();
  await expect(dialog.getByRole('alert')).toContainText('이 계좌에 이미 있어요');
  await expect(dialog.getByRole('button', { name: '저장', exact: true })).toBeDisabled();
});

for (const width of [320, 768, 1440]) test(`dashboard charts and holdings stay inside the page at ${width}px and 200% text`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.locator('.allocation-donut')).toBeVisible();
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: `test-results/dashboard-insights-${width}-${test.info().project.name}.png`, fullPage: true });
});
