import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fromPercents, toPercents } from '../src/bulkHoldings';
import { addCash, addHolding, toAsk } from './holdings';

// The analysis contract rejects weights that do not sum to exactly 100, so conversion must never leave a remainder or a zero weight.
// 비중으로 넣은 보유는 전체 금액을 원 단위로 나눠 보낸다. 합계가 전체 금액과 다르면 분석 원금이 달라진다.
test('splitting a total by weights keeps the exact total and leaves no positive weight at zero', () => {
  expect(fromPercents(1, [50, 50])).toEqual([1, 0]);
  expect(fromPercents(10000000, [33.33, 33.33, 33.34])).toEqual([3333000, 3333000, 3334000]);
  for (const [total, weights] of [[1001, [33.33, 33.33, 33.34]], [7, [0.01, 99.99]], [123456789, [12.34, 56.78, 30.88]]] as const) {
    const parts = fromPercents(total, [...weights]);
    expect(parts.reduce((sum, n) => sum + n, 0)).toBe(total);
    expect(parts.every(n => n >= 0)).toBe(true);
  }
});

test('converted weights always total exactly 100 with no zero holding', () => {
  expect(toPercents([3000000, 1000000])).toEqual(['75', '25']);
  // The last case raises 19 tiny holdings to the 0.01% floor, so the largest one must give the excess back.
  for (const amounts of [[1, 1, 1], [1, 1e12], [7, 13, 29, 31, 37], [...Array(19).fill(1), 1e12]]) {
    const weights = toPercents(amounts).map(Number);
    expect(weights.reduce((sum, n) => sum + Math.round(n * 100), 0)).toBe(10000);
    expect(weights.every(n => n > 0)).toBe(true);
  }
});

// 금액이 맞으면 금액이 전체 금액을 정한다 — 금액을 고친 뒤 다시 비중으로 바꾸면 예전 전체 금액이 남지 않는다.
test('switching back to percent after editing amounts takes the new amount total', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '10000');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await expect(page.locator('#total')).toHaveValue('1');
  await page.getByRole('button', { name: '금액', exact: true }).click();
  await page.getByLabel('현금 금액', { exact: true }).fill('20000');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await expect(page.locator('#total')).toHaveValue('2');
});

test('switching to percent converts amounts, keeps the total and shows the running sum as typed', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '3000000');
  await addHolding(page, 'VOO', 'Vanguard S&P 500 ETF', '1000000');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await expect(page.getByLabel('현금 비중', { exact: true })).toHaveValue('75');
  await expect(page.getByLabel('Vanguard S&P 500 ETF 비중', { exact: true })).toHaveValue('25');
  // 금액으로 적던 사람이 비중으로 바꿔도 전체 금액을 다시 묻지 않는다(금액 합계로 채운다).
  await expect(page.locator('#total')).toHaveValue('400');
  await expect(page.locator('.fl-basket')).toContainText('합계 100%');
  // A short total stays visible instead of being silently rescaled.
  await page.getByLabel('Vanguard S&P 500 ETF 비중', { exact: true }).fill('20');
  await expect(page.locator('.fl-basket')).toContainText('5% 남았어요');
  await expect(page.locator('#step-guidance')).toHaveText('비중 합계가 100%가 되면 넘어갈 수 있어요. 지금 95%예요.');
  await page.getByRole('button', { name: '금액', exact: true }).click();
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('3,000,000');
  await expect(page.getByLabel('Vanguard S&P 500 ETF 금액', { exact: true })).toHaveValue('1,000,000');
  await expect(page.locator('.fl-basket__total')).toHaveText('400만 원');
});

// 전체 금액이 없는 분석은 없다: 비중으로 넣으면 전체 금액을 받아 원 단위 금액으로 바꿔 보낸다(서버 계약은 금액 그대로).
test('percent input requires the total and is sent as whole-won amounts', async ({ page }) => {
  let posted: any;
  const catalog = JSON.parse(readFileSync(new URL('../../portfolio-worker/src/edge_portfolio/data/etf_catalog.json', import.meta.url), 'utf8'));
  await page.route('**/api/portfolio/**', route => {
    const url = route.request().url();
    if (url.endsWith('/analyses') && route.request().method() === 'POST') posted = route.request().postDataJSON();
    return route.fulfill({ json: url.endsWith('/catalog') ? catalog : url.includes('/analyses') ? { id: 'percent-job', state: 'queued' } : {} });
  });
  await page.goto('/portfolio/');
  await addCash(page, '1');
  await addHolding(page, 'VOO', 'Vanguard S&P 500 ETF', '1');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await page.getByLabel('현금 비중', { exact: true }).fill('75');
  await page.getByLabel('Vanguard S&P 500 ETF 비중', { exact: true }).fill('25');
  await page.locator('#total').fill('');
  await expect(page.locator('#step-guidance')).toHaveText('전체 금액을 적으면 넘어갈 수 있어요');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
  await page.locator('#total').fill('1,000');
  await toAsk(page);
  await page.getByRole('button', { name: '분석 시작하기' }).click();
  await expect.poll(() => posted?.input_mode).toBe('amount');
  expect(posted.holdings.map((h: { amount_krw: string }) => h.amount_krw)).toEqual(['7500000', '2500000']);
});

// Only the amount side of a pre-change draft is in 만 원; percentages must survive untouched.
for (const mode of ['amount', 'percent'] as const) test(`a ${mode}-mode draft saved in 만 원 reopens with the same won amount and untouched weights`, async ({ page }) => {
  const cash = (amount_krw: string) => [{ name: '현금', kind: 'cash', instrument_id: 'CASH:KRW', account: '일반 계좌', amount_krw }];
  const draft = { step: 0, holdings: cash(mode === 'amount' ? '123.4567' : '100'), alternate: mode === 'amount' ? [] : cash('123.4567'), selected: ['일반 계좌'], question: '', horizon: '', how: '', interests: [], note: '', contribution: '', keyword: '', tax: null, draftId: 'legacy', mode, fresh: false };
  await page.addInitScript(d => sessionStorage.setItem('edge-preview-form-v1', d), JSON.stringify(draft));
  await page.goto('/portfolio/?preview=input');
  if (mode === 'percent') {
    await expect(page.getByLabel('현금 비중', { exact: true })).toHaveValue('100');
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

// 비중으로 바꿔도 원금은 그대로여야 한다: 만 원 아래 금액도 잘라 내지 않고, 상한을 넘는 붙여넣기는 고치게 한다(잘라 맞추지 않는다).
test('the percent total keeps won precision and refuses an amount above the limit instead of truncating it', async ({ page }) => {
  let saved: any;
  const catalog = JSON.parse(readFileSync(new URL('../../portfolio-worker/src/edge_portfolio/data/etf_catalog.json', import.meta.url), 'utf8'));
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().includes('/drafts/')) saved = route.request().postDataJSON();
    return route.fulfill({ json: route.request().url().endsWith('/catalog') ? catalog : {} });
  });
  await page.goto('/portfolio/');
  await addCash(page, '15000');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await expect(page.locator('#total')).toHaveValue('1.5');
  await page.locator('#total').fill('1000000001');
  await expect(page.locator('#total')).toHaveValue('1,000,000,001');
  await expect(page.locator('#step-guidance')).toHaveText('전체 금액은 10조 원 이하로 적어 주세요.');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
  // 상한을 넘은 값 하나 때문에 쓰던 입력 전체를 버리지 않는다 — 새로 고쳐도 그 칸을 고치면 된다(아주 긴 붙여넣기도).
  await page.locator('#total').fill('100000000000000000000000');
  await page.reload();
  await expect(page.locator('#total')).toHaveValue('100,000,000,000,000');
  await expect(page.locator('#step-guidance')).toHaveText('전체 금액은 10조 원 이하로 적어 주세요.');
  await expect(page.getByLabel('현금 비중', { exact: true })).toHaveValue('100');
  // 원 단위로 나눌 수 없는 작은 전체 금액은 1원으로 부풀리지 않고 고치게 한다.
  await page.locator('#total').fill('0.0001');
  await page.getByLabel('현금 비중', { exact: true }).fill('50');
  await addHolding(page, '069500', 'KODEX 200');
  await page.getByLabel('KODEX 200 비중', { exact: true }).fill('50');
  await expect(page.locator('#step-guidance')).toContainText('전체 금액이 너무 작아');
  await page.getByRole('button', { name: 'KODEX 200 빼기' }).click();
  await page.getByLabel('현금 비중', { exact: true }).fill('100');
  await page.locator('#total').fill('12.5');
  await page.reload();
  await expect(page.locator('#total')).toHaveValue('12.5');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect.poll(() => saved?.holdings?.[0]?.amount_krw).toBe('125000');
});

test('the basket total keeps small and large amounts readable instead of rounding them away', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '15000');
  await expect(page.locator('.fl-basket__total')).toHaveText('1.5만 원');
  await page.getByLabel('현금 금액', { exact: true }).fill('9000');
  await expect(page.locator('.fl-basket__total')).toHaveText('9,000원');
  await page.getByLabel('현금 금액', { exact: true }).fill('99999999');
  await expect(page.locator('.fl-basket__total')).toHaveText('1억 원');
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
  await expect(page.getByRole('button', { name: '내 포트폴리오 넣기' })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('edge-preview-form-v1'))).toBeNull();
});
