import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { bulkHoldings } from '../src/bulkHoldings';
const catalog = JSON.parse(readFileSync(new URL('../../portfolio-worker/src/edge_portfolio/data/etf_catalog.json', import.meta.url), 'utf8'));

test('bulk input resolves exact codes and rejects duplicates and account mismatch', () => {
  expect(bulkHoldings('VOO 60\nQQQ 40', catalog, '일반 계좌', 'percent').map(row => row.amount_krw)).toEqual(['60', '40']);
  expect(() => bulkHoldings('VOO 50\nVOO 50', catalog, '일반 계좌', 'percent')).toThrow('같은 종목');
  expect(() => bulkHoldings('VOO 100', catalog, 'ISA', 'amount')).toThrow('일반 계좌');
  expect(() => bulkHoldings('unknown 100', catalog, '일반 계좌', 'amount')).toThrow('정확한 종목');
  // Amounts are whole won as brokerage apps show them, with or without separators and a trailing 원.
  expect(bulkHoldings('VOO 1,500,000', catalog, '일반 계좌', 'amount')[0].amount_krw).toBe('1500000');
  expect(bulkHoldings('VOO 5,000,000원', catalog, '일반 계좌', 'amount')[0].amount_krw).toBe('5000000');
  expect(() => bulkHoldings('VOO 123.4567', catalog, '일반 계좌', 'amount')).toThrow();
  expect(() => bulkHoldings('VOO 1.001', catalog, '일반 계좌', 'percent')).toThrow();
  for (const number of ['1,5', '1,,000', '1000,', '12,34,567']) expect(() => bulkHoldings(`VOO ${number}`, catalog, '일반 계좌', 'amount')).toThrow();
});

test('failed analysis retries the server saved input without reconstructing it from a draft', async ({ page }) => {
  const input = { holdings: [{ name: 'VOO', instrument_id: 'US:VOO', kind: 'etf', account: '일반 계좌', amount_krw: '1234500' }], horizon: '10년 이상', question: '보유 의도 유지', input_mode: 'amount' };
  let posted: unknown;
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'failed'));
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/analyses') && route.request().method() === 'POST') {
      posted = route.request().postDataJSON();
      return route.fulfill({ json: { id: 'retry', state: 'queued', input } });
    }
    if (path.endsWith('/analyses/failed')) return route.fulfill({ json: { id: 'failed', state: 'failed', input: { ...input, draft_id: 'expired-editable-draft' } } });
    if (path.endsWith('/analyses/retry')) return route.fulfill({ json: { id: 'retry', state: 'queued', input } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await page.getByRole('button', { name: '같은 입력으로 다시 분석' }).click();
  await expect.poll(() => posted).toEqual(input);
  await expect(page.locator('.progress')).toBeVisible();
});

for (const blockedStorage of [false, true]) test(`failed input remains editable without a draft (blocked storage: ${blockedStorage})`, async ({ page }) => {
  let posted: any;
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'failed'));
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().url().endsWith('/analyses') && route.request().method() === 'POST') {
      posted = route.request().postDataJSON();
      return route.fulfill({ json: { id: 'restored', state: 'queued' } });
    }
    return route.fulfill({ json: { id: 'failed', state: 'failed', input: { holdings: [
      { name: 'VOO', instrument_id: 'US:VOO', kind: 'etf', account: '일반 계좌', amount_krw: '1234567' }],
      horizon: '10년 이상', question: '', input_mode: 'amount', preferences: { accounts: ['일반 계좌'], contribution: '이미 다 매수한 상태에요', dividend_goal: '매월 40만 원', interests: [], note: '', tax_profile: null } } } });
  });
  await page.goto('/portfolio/');
  if (blockedStorage) await page.evaluate(() => Object.defineProperty(window, 'sessionStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } }));
  await page.getByRole('button', { name: '입력 수정하기' }).click();
  await expect(page.getByRole('textbox', { name: 'VOO 금액' })).toHaveValue('1,234,567');
  await expect(page.getByLabel('기존 배당 목표')).toHaveValue('매월 40만 원');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '선택 질문 건너뛰고 분석하기' }).click();
  await expect.poll(() => posted?.preferences.dividend_goal).toBe('매월 40만 원');
  expect(posted.holdings[0].amount_krw).toBe('1234567');
});

test('bulk entry reaches analysis without optional question screens', async ({ page }) => {
  let posted: any;
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().url().endsWith('/analyses')) posted = route.request().postDataJSON();
    return route.fulfill({ json: { id: 'bulk', state: 'queued' } });
  });
  await page.goto('/portfolio/');
  await page.getByText('여러 종목 한 번에 넣기', { exact: true }).click();
  await page.getByLabel('일반 계좌 · 종목 코드와 금액(원)').fill('VOO 600,000\nQQQ 400000원');
  await page.getByRole('button', { name: '목록에 추가' }).click();
  await expect(page.locator('.row')).toHaveCount(2);
  await page.screenshot({ path: `test-results/bulk-entry-${test.info().project.name}.png`, fullPage: true });
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '이미 다 매수한 상태에요', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '10년 넘게 뒤', exact: true }).click();
  await page.getByRole('button', { name: '선택 질문 건너뛰고 분석하기' }).click();
  await expect.poll(() => posted?.holdings.length).toBe(2);
  expect(posted.holdings.map((h: { amount_krw: string }) => h.amount_krw)).toEqual(['600000', '400000']);
  expect(posted.preferences.interests).toEqual([]);
});

test('saved stocks are not silently relabeled as ETFs when reusing holdings for analysis', async ({ page }) => {
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().url().endsWith('/workbook')) return route.fulfill({ json: { version: 1, workbook: { assets: [{ id: 'a', instrument_id: 'US:AAPL', name: 'Apple', account: '일반 계좌', category: '주식', amount: 1000000 }] } } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await page.getByRole('button', { name: '저장된 보유 종목 불러오기' }).click();
  await expect(page.getByRole('alert')).toContainText('Apple은 ETF 분석 대상이 아니에요.');
  await expect(page.locator('.ds-confirm-dialog')).not.toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Apple 금액' })).toHaveCount(0);
});

for (const state of ['missing', 'pending', 'old-version', 'no-market']) test(`reuse leaves ${state} quotes blank instead of importing stale value`, async ({ page }) => {
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().url().endsWith('/workbook')) return route.fulfill({ json: {
      version: 1, workbook: { assets: [{ id: 'a', instrument_id: 'US:VOO', name: 'VOO', account: '일반 계좌', category: '주식', amount: 1000000 }] },
      tracking: { market: state === 'no-market' ? null : { version: state === 'old-version' ? 0 : 1, payload: {
        assets: [{ id: 'a', value: 2000000 }], missing: state === 'missing' ? ['a'] : [], pending_prices: state === 'pending' ? ['a'] : []
      } } }
    } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await page.getByRole('button', { name: '저장된 보유 종목 불러오기' }).click();
  await page.locator('.ds-confirm-dialog').getByRole('button', { name: '불러오기', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'VOO 금액' })).toHaveValue('');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
});

for (const blockedStorage of [false, true]) test(`reuse preserves whole-won balances (blocked storage: ${blockedStorage})`, async ({ page }) => {
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().url().endsWith('/workbook')) return route.fulfill({ json: {
      version: 1, workbook: { assets: [
        { id: 'a', instrument_id: 'US:VOO', name: 'VOO', account: '일반 계좌', category: '주식' },
        { id: 'c', instrument_id: 'CASH:KRW', name: '현금', account: '일반 계좌', category: '현금', amount: 49 }
      ] }, tracking: { market: { version: 1, payload: { assets: [{ id: 'a', value: 1234567 }], missing: [], pending_prices: [] } } }
    } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  if (blockedStorage) await page.evaluate(() => Object.defineProperty(window, 'sessionStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } }));
  await page.getByRole('button', { name: '저장된 보유 종목 불러오기' }).click();
  await page.locator('.ds-confirm-dialog').getByRole('button', { name: '불러오기', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '현금 금액' })).toHaveValue('49');
  await expect(page.getByRole('textbox', { name: 'VOO 금액' })).toHaveValue('1,234,567');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeEnabled();
});

for (const priorState of ['failed', 'queued', 'running', 'succeeded']) test(`tab-local pending request survives a shared ${priorState} job pointer`, async ({ page }) => {
  const input = { holdings: [], horizon: '10년 이상', planning: true };
  await page.addInitScript(input => {
    localStorage.setItem('edge-portfolio-job', 'failed');
    sessionStorage.setItem('edge-portfolio-pending', JSON.stringify({ input: JSON.stringify(input), key: 'same-retry-key' }));
  }, input);
  let posted: unknown;
  let key: string | undefined;
  await page.route('**/api/portfolio/**', route => {
    if (route.request().method() === 'POST' && route.request().url().endsWith('/analyses')) {
      posted = route.request().postDataJSON();
      key = route.request().headers()['idempotency-key'];
      return route.fulfill({ json: { id: 'retry', state: 'queued', input } });
    }
    return route.fulfill({ json: route.request().url().endsWith('/analyses/failed')
      ? { id: 'failed', state: priorState, input } : { id: 'retry', state: 'queued', input } });
  });
  await page.goto('/portfolio/');
  await expect.poll(() => posted).toEqual(input);
  expect(key).toBe('same-retry-key');
  await expect(page.locator('.progress')).toBeVisible();
});

test('a completed report can start fresh when browser session storage is unavailable', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-feedback:preview', 'done'));
  await page.goto('/portfolio/?preview=result');
  await expect(page.getByRole('button', { name: '새 포트폴리오 분석하기', exact: true })).toBeVisible();
  await page.evaluate(() => Object.defineProperty(window, 'sessionStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } }));
  await page.getByRole('button', { name: '새 포트폴리오 분석하기', exact: true }).click();
  await expect(page.getByRole('button', { name: '저장된 보유 종목 불러오기', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '새 포트폴리오 분석하기', exact: true })).toHaveCount(0);
});

test('failed reconciliation retries the pending request instead of showing an older report', async ({ page }) => {
  const input = { holdings: [], planning: true, horizon: '10년 이상' };
  await page.addInitScript(input => {
    localStorage.setItem('edge-portfolio-job', 'old');
    sessionStorage.setItem('edge-portfolio-pending', JSON.stringify({ input: JSON.stringify(input), key: 'unresolved' }));
  }, input);
  const requests: { input: unknown; key: string }[] = [];
  await page.route('**/api/portfolio/**', route => {
    if (route.request().method() === 'POST' && route.request().url().endsWith('/analyses')) {
      requests.push({ input: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
      if (requests.length === 1) return route.abort();
      return route.fulfill({ json: { id: 'recovered', state: 'queued' } });
    }
    return route.fulfill({ json: route.request().url().endsWith('/analyses/old')
      ? { id: 'old', state: 'succeeded', result: {} } : { id: 'recovered', state: 'queued' } });
  });
  await page.goto('/portfolio/');
  await expect(page.getByRole('button', { name: '접수 다시 확인' })).toBeVisible();
  await expect(page.locator('#report')).toHaveCount(0);
  await page.getByRole('button', { name: '접수 다시 확인' }).click();
  await expect(page.locator('.progress')).toBeVisible();
  expect(requests).toEqual([{ input, key: 'unresolved' }, { input, key: 'unresolved' }]);
});

for (const blockedStorage of [false, true]) test(`a rejected historical retry restores editable input (blocked storage: ${blockedStorage})`, async ({ page }) => {
  const input = { holdings: [{ name: 'VOO', instrument_id: 'US:VOO', kind: 'etf', account: '일반 계좌', amount_krw: '1234500' }], horizon: '10년 이상', question: '', input_mode: 'amount' };
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'failed'));
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().method() === 'POST' && route.request().url().endsWith('/analyses')) return route.fulfill({ status: 422, json: { detail: 'CONTRIBUTION_CHOICE_REQUIRED' } });
    if (route.request().url().endsWith('/analyses/failed')) return route.fulfill({ json: { id: 'failed', state: 'failed', input } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await expect(page.getByRole('button', { name: '같은 입력으로 다시 분석' })).toBeVisible();
  if (blockedStorage) await page.evaluate(() => Object.defineProperty(window, 'sessionStorage', { get() { throw new DOMException('blocked', 'SecurityError'); } }));
  await page.getByRole('button', { name: '같은 입력으로 다시 분석' }).click();
  await expect(page.getByRole('textbox', { name: 'VOO 금액' })).toHaveValue('1,234,500');
  await expect(page.getByRole('alert').filter({ hasText: '투자금을 넣는 방법' })).toBeVisible();
  await expect(page.getByRole('button', { name: '접수 다시 확인' })).toHaveCount(0);
});

test('an unidentified saved asset cannot replace and invalidate the current draft', async ({ page }) => {
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().url().endsWith('/workbook')) return route.fulfill({ json: {
      version: 1, workbook: { assets: [{ id: 'legacy', name: '이전 종목', instrument_id: null, category: '주식', account: '일반 계좌', amount: 1000000 }] }
    } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('요청사항', { exact: true }).fill('기존 방향을 유지해 주세요');
  await page.getByRole('button', { name: '저장된 보유 종목 불러오기' }).click();
  await expect(page.getByRole('alert')).toContainText('이전 종목의 종목 코드');
  await page.reload();
  await expect(page.getByLabel('요청사항', { exact: true })).toHaveValue('기존 방향을 유지해 주세요');
  await expect(page.locator('.rows .row')).toHaveCount(0);
});
