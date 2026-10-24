import { test, expect, type Page } from '@playwright/test';
import type { Asset, Saved } from '../src/workbook';

const asset: Asset = { id: 'holding', instrument_id: 'KR:069500', name: 'KODEX 200', account: 'ISA', category: '주식', currency: 'KRW', quantity: null, average: null, price: null, cost_fx: null, amount: 1000000, weight: null, target: null, fee: 0, price_date: null };
async function setup(page: Page, subscribed = false) {
  let trackingReads = 0, subscriptions = 0;
  const saved: Saved = { version: 1, workbook: { title: '내 포트폴리오', fx: null, assets: [asset], dividends: [], months: [] }, tracking: { market: { version: 1, payload: { date: '2026-10-20', assets: [{ id: 'holding', instrument_id: 'KR:069500', value: 1000000, profit: null, price: 50000, price_date: '2026-10-19', quantity: 20, average: null, estimated_quantity: true }], fx: null, forecasts: [], missing: [], dividend_missing: [] } }, days: [] } };
  await page.route('**/api/portfolio/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/newsletter')) {
      if (route.request().method() === 'POST') { subscriptions++; expect(route.request().postDataJSON()).toEqual({ email: 'reader@example.test', consent: true }); return route.fulfill({ json: { status: 'pending', delivery_ready: true } }); }
      return route.fulfill({ json: { subscribed } });
    }
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/catalog')) return route.fulfill({ json: { instruments: [] } });
    if (path.endsWith('/tracking')) { trackingReads++; return route.fulfill({ json: saved.tracking }); }
    if (path.endsWith('/workbook')) return route.fulfill({ json: saved });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.clock.install();
  await page.goto('/portfolio/?view=dashboard');
  await expect(page.locator('.holding-edit')).toBeVisible();
  return { reads: () => trackingReads, subscriptions: () => subscriptions };
}

test('saved quotes arrive with the workbook without a second tracking waterfall', async ({ page }) => {
  const state = await setup(page, true);
  await expect(page.locator('.dashboard-stats')).toContainText('1,000,000원');
  expect(state.reads()).toBe(0);
});

test('missing inputs are highlighted until opening the editor and email never interrupts editing', async ({ page }) => {
  await setup(page);
  const row = page.locator('.holdings-table tbody tr');
  await expect(row).toHaveAttribute('data-needs-input', 'true');
  await expect(row).toContainText('평단가와 수량을 입력해요!');
  await page.screenshot({ path: `test-results/holding-onboarding-${test.info().project.name}.png`, fullPage: true });
  await page.locator('.holding-edit').click();
  await expect(row).toHaveAttribute('data-needs-input', 'false');
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('');
  await page.clock.fastForward(12000);
  await expect(page.getByRole('dialog', { name: '내 ETF 소식, 매일 짧게' })).not.toBeVisible();
  await page.getByRole('button', { name: '수정 닫기' }).click();
  await expect(page.getByRole('dialog', { name: '내 ETF 소식, 매일 짧게' })).toBeVisible();
  await page.getByRole('button', { name: '나중에 할게요' }).click();
  await expect(page.locator('.holding-edit')).toBeFocused();
  await expect(row).toHaveAttribute('data-needs-input', 'false');
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  await page.getByRole('button', { name: '한눈에', exact: true }).click();
  await expect(row).toHaveAttribute('data-needs-input', 'false');
});

test('email appears after ten seconds and never subscribes before explicit submission', async ({ page }) => {
  const state = await setup(page);
  await page.clock.fastForward(9000);
  await expect(page.locator('.dashboard-newsletter')).not.toBeVisible();
  await page.clock.fastForward(1000);
  await expect(page.locator('.dashboard-newsletter')).toBeVisible();
  expect(state.subscriptions()).toBe(0);
  const preview = page.locator('.dashboard-newsletter .newsletter-example img');
  await expect(preview).toBeVisible();
  await expect.poll(() => preview.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await expect(page.locator('.dashboard-newsletter blockquote')).toHaveCount(0);
  await page.screenshot({ path: `test-results/newsletter-popup-${test.info().project.name}.png`, fullPage: true });
  await page.getByLabel('뉴스레터 이메일').fill('reader@example.test');
  await page.getByRole('button', { name: '이메일로 받아보기' }).click();
  await expect(page.locator('.dashboard-newsletter')).toContainText('확인 메일의 링크');
  expect(state.subscriptions()).toBe(1);
  await page.screenshot({ path: `test-results/dashboard-email-${test.info().project.name}.png`, fullPage: true });
  await page.keyboard.press('Escape');
  await page.reload();
  await page.clock.fastForward(12000);
  await expect(page.locator('.dashboard-newsletter')).not.toBeVisible();
});

test('existing subscribers are never asked again', async ({ page }) => {
  const state = await setup(page, true);
  await page.clock.fastForward(12000);
  await expect(page.locator('.dashboard-newsletter')).not.toBeVisible();
  expect(state.subscriptions()).toBe(0);
});
