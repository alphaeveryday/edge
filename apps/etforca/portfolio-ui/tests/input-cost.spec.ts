import { test, expect } from '@playwright/test';
import { toPercents } from '../src/bulkHoldings';
import { addCash } from './holdings';

// The analysis contract rejects weights that do not sum to exactly 100, so conversion must never leave a remainder or a zero weight.
test('converted weights always total exactly 100 with no zero holding', () => {
  expect(toPercents([3000000, 1000000])).toEqual(['75', '25']);
  for (const amounts of [[1, 1, 1], [1, 1e12], [7, 13, 29, 31, 37]]) {
    const weights = toPercents(amounts).map(Number);
    expect(weights.reduce((sum, n) => sum + Math.round(n * 100), 0)).toBe(10000);
    expect(weights.every(n => n > 0)).toBe(true);
  }
});

test('switching to percent converts amounts and shows the running total as typed', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '3000000');
  await page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' }).fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard' }).click();
  await page.keyboard.type('1000000');
  await page.getByRole('button', { name: '비율', exact: true }).click();
  await expect(page.getByLabel('현금 비율', { exact: true })).toHaveValue('75');
  await expect(page.getByLabel('Vanguard S&P 500 ETF 비율', { exact: true })).toHaveValue('25');
  await expect(page.locator('.unit__note')).toContainText('100%를 맞췄어요');
  // A short total stays visible instead of being silently rescaled in the preview.
  await page.getByLabel('Vanguard S&P 500 ETF 비율', { exact: true }).fill('20');
  await expect(page.locator('.unit__note')).toContainText('합계 95% · 5% 남았어요');
  await expect(page.locator('.portfolio-preview li').filter({ hasText: 'Vanguard' })).toContainText('20%');
  await expect(page.locator('#step-guidance')).toHaveText('비중 합계를 100%로 맞춰 주세요. 지금 95%예요.');
  await page.getByRole('button', { name: '금액', exact: true }).click();
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('3,000,000');
  await expect(page.getByLabel('Vanguard S&P 500 ETF 금액', { exact: true })).toHaveValue('1,000,000');
  await expect(page.locator('.portfolio-preview .donut__c')).toContainText('400만');
});

test('a draft saved in 만 원 before the unit change reopens as the same amount in won', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('edge-preview-form-v1', JSON.stringify({ step: 0, holdings: [{ name: '현금', kind: 'cash', instrument_id: 'CASH:KRW', account: '일반 계좌', amount_krw: '123.4567' }], alternate: [], selected: ['일반 계좌'], question: '', horizon: '', how: '', interests: [], note: '', contribution: '', keyword: '', tax: null, draftId: 'legacy', mode: 'amount', fresh: false })));
  await page.goto('/portfolio/?preview=input');
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('1,234,567');
});
