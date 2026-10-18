import { test, expect } from '@playwright/test';

test('switching to an account without old dividends keeps the selected year truthful', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-10-18T03:00:00Z'));
  const base = { instrument_id: 'US:SCHD', name: 'SCHD', category: '주식', currency: 'USD', quantity: 10, average: 25, price: null, cost_fx: null, amount: null, weight: null, target: null, fee: 0, price_date: null };
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/workbook')) return route.fulfill({ json: { version: 1, workbook: { title: '내 포트폴리오', fx: null, assets: [{ ...base, id: 'a', account: '일반 계좌' }, { ...base, id: 'b', account: 'ISA' }], months: [], dividends: [{ id: 'd', asset_id: 'a', date: '2025-06-01', status: '실수령', amount: 1234 }] } } });
    return route.fulfill({ json: path.endsWith('/tracking') ? { market: null, days: [] } : {} });
  });
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  const report = page.locator('.dividend-report');
  await report.getByRole('button', { name: '실수령 기록', exact: true }).click();
  await report.getByLabel('연도').selectOption('2025');
  await expect(report.locator('tfoot td').last()).toHaveText('1,234원');
  await page.locator('.dashboard-main header select').selectOption('ISA');
  await expect(report.getByLabel('연도')).toHaveValue('2025');
  await expect(report.getByLabel('연도').locator('option:checked')).toHaveText('2025년');
  await expect(report.locator('tfoot td').last()).toHaveText('—');
  await expect(report.getByRole('button', { name: '2025-01 실수령 미기록', exact: true })).toBeVisible();
});

for (const observed of ['2026-09-30', '2026-10-16', 'zero', 'mixed', 'partial', 'stale']) test(`a valuation date cannot extend dividend observation coverage: ${observed}`, async ({ page }) => {
  const day = observed === '2026-09-30' ? '2026-10-01' : '2026-10-18';
  await page.clock.setFixedTime(new Date(`${day}T03:00:00Z`));
  const asset = { id: 'a', instrument_id: 'US:SCHD', name: 'SCHD', account: '일반 계좌', category: '주식', currency: 'USD', quantity: observed === 'zero' ? 0 : 10, average: 25, price: null, cost_fx: null, amount: null, weight: null, target: null, fee: 0, price_date: null };
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/workbook')) return route.fulfill({ json: { version: 1, workbook: { title: '내 포트폴리오', fx: null, assets: observed === 'mixed' ? [asset, { ...asset, id: 'zero', quantity: 0 }] : [asset], months: [], dividends: [] } } });
    return route.fulfill({ json: path.endsWith('/tracking') ? { market: { version: 1, payload: { date: observed === 'stale' ? '2026-10-01' : day, assets: [{ id: 'a', value: 350000, profit: 0, quantity: 10, average: 25, price: 25, price_date: observed === 'zero' ? null : ['mixed', 'partial', 'stale'].includes(observed) ? '2026-10-16' : observed }], fx: 1400, forecasts: observed === 'partial' ? [{ asset_id: 'a', month: '2027-10', amount: 1234 }] : [], missing: [], dividend_missing: [] } }, days: [] } : {} });
  });
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  if (observed === 'stale') {
    await expect(page.locator('.dividend-metrics')).toContainText('확인된 예상');
    await expect(page.locator('.dividend-metrics strong').first()).toHaveText('—');
  }
  const calendar = page.locator('.dashboard-card').filter({ has: page.getByRole('heading', { name: '배당 달력', exact: true }) });
  await expect(calendar.getByRole('button', { name: observed === 'zero' ? '2027-10 예상 0원' : observed === 'partial' ? '2027-10 확인된 예상 1,234원' : '2027-10 예상 미확인', exact: true })).toBeAttached();
  const report = page.locator('.dividend-report');
  await report.getByLabel('연도').selectOption('2027');
  await expect(report.locator('tbody tr').first().locator('td').nth(8)).toHaveText('0');
  await expect(report.locator('tbody tr').first().locator('td').nth(9)).toHaveText(observed === 'zero' ? '0' : observed === 'partial' ? '1,234' : '—');
  await expect(report.getByRole('button', { name: observed === 'zero' ? '2027-10 예상 0원' : observed === 'partial' ? '2027-10 확인된 예상 1,234원' : '2027-10 예상 미확인', exact: true })).toBeVisible();
});
