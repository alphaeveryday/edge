import { test, expect } from '@playwright/test';
import { toPercents } from '../src/bulkHoldings';
import { addCash } from './holdings';

// The analysis contract rejects weights that do not sum to exactly 100, so conversion must never leave a remainder or a zero weight.
test('converted weights always total exactly 100 with no zero holding', () => {
  expect(toPercents([3000000, 1000000])).toEqual(['75', '25']);
  // The last case raises 19 tiny holdings to the 0.01% floor, so the largest one must give the excess back.
  for (const amounts of [[1, 1, 1], [1, 1e12], [7, 13, 29, 31, 37], [...Array(19).fill(1), 1e12]]) {
    const weights = toPercents(amounts).map(Number);
    expect(weights.reduce((sum, n) => sum + Math.round(n * 100), 0)).toBe(10000);
    expect(weights.every(n => n > 0)).toBe(true);
  }
});

test('switching to percent converts amounts and shows the running total as typed', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '3000000');
  await page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' }).fill('VOO');
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

// Only the amount side of a pre-change draft is in 만 원; percentages must survive untouched.
for (const mode of ['amount', 'percent'] as const) test(`a ${mode}-mode draft saved in 만 원 reopens with the same won amount and untouched weights`, async ({ page }) => {
  const cash = (amount_krw: string) => [{ name: '현금', kind: 'cash', instrument_id: 'CASH:KRW', account: '일반 계좌', amount_krw }];
  const draft = { step: 0, holdings: cash(mode === 'amount' ? '123.4567' : '100'), alternate: mode === 'amount' ? [] : cash('123.4567'), selected: ['일반 계좌'], question: '', horizon: '', how: '', interests: [], note: '', contribution: '', keyword: '', tax: null, draftId: 'legacy', mode, fresh: false };
  await page.addInitScript(d => sessionStorage.setItem('edge-preview-form-v1', d), JSON.stringify(draft));
  await page.goto('/portfolio/?preview=input');
  if (mode === 'percent') {
    await expect(page.getByLabel('현금 비율', { exact: true })).toHaveValue('100');
    await page.getByRole('button', { name: '금액', exact: true }).click();
  }
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('1,234,567');
  // Migrated once: the old 만 원 draft cannot come back after this draft is cleared, nor be read by a rolled-back bundle.
  expect(await page.evaluate(() => [sessionStorage.getItem('edge-preview-form-v1'), !!sessionStorage.getItem('edge-preview-form-v2')])).toEqual([null, true]);
});

test('a pasted decimal amount keeps its won value instead of shifting 100x', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '1,234,567.89');
  const amount = page.getByLabel('현금 금액', { exact: true });
  await expect(amount).toHaveValue('1,234,567');
  // A stray dot from a numeric keypad or a 1.234.567-style paste must not erase the digits after it.
  await amount.fill('1.234.567');
  await expect(amount).toHaveValue('1,234,567');
  await amount.fill('1.,234,567');
  await expect(amount).toHaveValue('1,234,567');
  await amount.fill('1,234,567.89원');
  await expect(amount).toHaveValue('1,234,567');
  // Too many digits is shown and refused, never silently cut down to a valid amount.
  await amount.fill('100000000000000');
  await expect(amount).toHaveValue('100,000,000,000,000');
  await expect(page.locator('#step-guidance')).toHaveText('금액은 10조 원 이하로 적어 주세요.');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
});

test('the preview legend keeps small and large amounts readable instead of rounding them away', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '15000');
  await page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' }).fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard' }).click();
  // VOO is still empty, so the legend shows amounts rather than weights.
  await expect(page.locator('.portfolio-preview li').filter({ hasText: '현금' })).toContainText('1.5만 원');
  await page.getByRole('button', { name: 'Vanguard S&P 500 ETF 빼기' }).click();
  await expect(page.locator('.portfolio-preview .donut__c')).toContainText('1.5만');
  await page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' }).fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard' }).click();
  await page.getByLabel('현금 금액', { exact: true }).fill('99999999');
  await expect(page.locator('.portfolio-preview li').filter({ hasText: '현금' })).toContainText('1억 원');
});

// A tab that was mid-analysis at deploy keeps its v1 draft while a v2 draft is restored from the server;
// starting over must not resurrect the pre-deploy input.
test('starting over after a restored draft does not bring back a leftover 만 원 draft', async ({ page }) => {
  const cash = { name: '현금', kind: 'cash', instrument_id: 'CASH:KRW', account: '일반 계좌' };
  const base = { step: 0, alternate: [], selected: ['일반 계좌'], question: '', horizon: '', how: '', interests: [], note: '', contribution: '', keyword: '', tax: null, mode: 'amount', fresh: false };
  await page.addInitScript(([v1, v2]) => {
    if (sessionStorage.getItem('seeded')) return;
    sessionStorage.setItem('seeded', '1');
    sessionStorage.setItem('edge-preview-form-v1', v1);
    sessionStorage.setItem('edge-preview-form-v2', v2);
  }, [JSON.stringify({ ...base, draftId: 'old', holdings: [{ ...cash, amount_krw: '7' }] }), JSON.stringify({ ...base, draftId: 'new', holdings: [{ ...cash, amount_krw: '5000' }] })]);
  await page.goto('/portfolio/?preview=input');
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('5,000');
  await page.getByRole('button', { name: '새로 작성', exact: true }).click();
  await page.getByRole('button', { name: '새로 시작하기', exact: true }).click();
  await expect(page.locator('.rows .row')).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem('edge-preview-form-v1'))).toBeNull();
});
