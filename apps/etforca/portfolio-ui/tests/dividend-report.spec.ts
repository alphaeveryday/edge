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
