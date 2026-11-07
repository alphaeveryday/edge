import { test, expect, type Page } from '@playwright/test';
import type { Asset, Saved, Workbook } from '../src/workbook';

const asset: Asset = { id: 'original', instrument_id: 'KR:069500', name: 'KODEX 200', account: 'ISA', category: '기타', currency: 'KRW', quantity: null, amount: 1234567, weight: null, average: null, price: null, cost_fx: null, target: null, fee: 0, price_date: null };
const source: Workbook = { title: '내 포트폴리오', assets: [asset], dividends: [], months: [], fx: null };
const section = { paragraphs: ['입력과 다른 추천 결과'], evidence: [], limitations: [] };
async function setup(page: Page, state = 'succeeded', original = source, initial: Saved = { version: 0, workbook: null }) {
  const data = { saved: initial, writes: [] as { version: number; workbook: Workbook }[] };
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'save-input'));
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: { is_test: true } });
    if (path.endsWith('/catalog')) return route.fulfill({ json: { instruments: [] } });
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: [] } });
    if (path.endsWith('/tracking')) return route.fulfill({ json: { market: null, days: [] } });
    if (path.endsWith('/analyses/save-input/workbook')) return route.fulfill({ json: original });
    if (path.endsWith('/workbook')) {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON(); data.writes.push(body);
        if (body.version !== data.saved.version) return route.fulfill({ status: 409, json: { detail: 'WORKBOOK_CONFLICT' } });
        data.saved = { version: body.version + 1, workbook: body.workbook };
      }
      return route.fulfill({ json: data.saved });
    }
    if (path.endsWith('/analyses/save-input') || path.endsWith('/analyses/latest')) return route.fulfill({ json: {
      id: 'save-input', state, failure: state === 'failed' ? 'ANALYSIS_FAILED' : null,
      input: { holdings: [{ name: asset.name, account: asset.account, amount_krw: String(asset.amount) }], horizon: '10년 이상' },
      result: state === 'failed' ? null : { sections: { question: section, portfolio: section, outlook: section, opinion: section },
        metrics: { holdings: [], total_krw: '99999999', unavailable_reason: '' }, sources: [], as_of: '2026-10-28', model: 'test', elapsed_seconds: 0 },
    } });
    return route.fulfill({ json: {} });
  });
  return data;
}
const saveButton = (page: Page) => page.getByRole('button', { name: '입력한 포트폴리오 저장', exact: true });

for (const state of ['succeeded', 'failed']) test(`original input survives ${state}, save and refresh without using recommendation values`, async ({ page }) => {
  const data = await setup(page, state);
  await page.goto('/portfolio/');
  await saveButton(page).scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath('input-save-action.png') });
  await saveButton(page).click();
  await expect(page).toHaveURL(/view=dashboard/);
  expect(data.writes).toEqual([{ version: 0, workbook: source }]);
  await page.reload();
  await expect(page.locator('.holding-edit')).toContainText(asset.name);
  expect(data.saved.workbook?.assets[0].amount).toBe(1234567);
  expect(data.saved.workbook?.assets[0].quantity).toBeNull();
});

test('replacement requires confirmation and preserves existing holdings and history on cancellation', async ({ page }) => {
  const previous = { ...source, title: '기존 포트폴리오', months: [{ month: '2026-09', value: 100, deposit: 0, withdrawal: 0, return_pct: null }] };
  const data = await setup(page, 'succeeded', source, { version: 7, workbook: previous });
  await page.goto('/portfolio/');
  await saveButton(page).click();
  await expect(page.getByRole('dialog')).toContainText('기존 포트폴리오');
  await page.keyboard.press('Escape');
  await expect(saveButton(page)).toBeFocused();
  expect(data.writes).toEqual([]);
  expect(data.saved.workbook).toEqual(previous);
});

test('a concurrent edit cannot be overwritten using an old confirmation; retry reads and confirms the new version', async ({ page }) => {
  const data = await setup(page, 'succeeded', source, { version: 3, workbook: source });
  await page.goto('/portfolio/');
  await saveButton(page).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  data.saved = { version: 4, workbook: { ...source, title: '다른 탭의 최신 보유' } };
  await page.getByRole('button', { name: '입력한 보유로 교체' }).click();
  await expect(page.getByRole('region', { name: '입력한 포트폴리오 보관' }).getByRole('alert')).toContainText('다른 탭');
  expect(data.saved.version).toBe(4);
  await expect(page).not.toHaveURL(/view=dashboard/);
  await saveButton(page).click();
  await expect(page.getByRole('dialog')).toContainText('다른 탭의 최신 보유');
  await page.getByRole('button', { name: '입력한 보유로 교체' }).click();
  await expect(page).toHaveURL(/view=dashboard/);
  expect(data.writes.map(w => w.version)).toEqual([3, 4]);
});

for (const resource of ['source', 'saved']) test(`failed ${resource} read cannot write or discard the analysis`, async ({ page }) => {
  const data = await setup(page);
  const url = resource === 'source' ? '**/analyses/save-input/workbook' : '**/api/portfolio/workbook';
  await page.route(url, route => route.abort());
  await page.goto('/portfolio/');
  await saveButton(page).click();
  await expect(page.getByRole('region', { name: '입력한 포트폴리오 보관' }).getByRole('alert')).toBeVisible();
  expect(data.writes).toEqual([]);
  await expect(page.locator('#result')).toBeVisible();
});

test('lost save response offers reading the server state without automatically writing again', async ({ page }) => {
  const data = await setup(page);
  await page.route('**/api/portfolio/workbook', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON(); data.writes.push(body);
    data.saved = { version: body.version + 1, workbook: body.workbook };
    return route.abort();
  });
  await page.goto('/portfolio/');
  await saveButton(page).click();
  await expect(page.getByRole('region', { name: '입력한 포트폴리오 보관' }).getByRole('alert')).toBeVisible();
  await expect(page).not.toHaveURL(/view=dashboard/);
  expect(data.writes).toHaveLength(1);
  await page.getByRole('link', { name: '저장된 포트폴리오 확인' }).click();
  await expect(page.locator('.holding-edit')).toContainText(asset.name);
  expect(data.writes).toHaveLength(1);
});

test('empty original holdings cannot erase an existing workbook', async ({ page }) => {
  const data = await setup(page, 'failed', { ...source, assets: [] }, { version: 2, workbook: source });
  await page.goto('/portfolio/');
  await saveButton(page).click();
  await expect(page.getByRole('region', { name: '입력한 포트폴리오 보관' }).getByRole('alert')).toContainText('저장할 보유 종목이 없어요');
  expect(data.writes).toEqual([]);
  expect(data.saved.workbook).toEqual(source);
});

test('legacy percentage input needs real total money before saving', async ({ page }) => {
  const data = await setup(page, 'failed', { ...source, assets: [{ ...asset, amount: null, weight: 100 }] });
  await page.goto('/portfolio/');
  await saveButton(page).click();
  await page.getByLabel('총 투자금액 (원)').fill('2345678');
  expect(data.writes).toEqual([]);
  await page.getByRole('button', { name: '금액 적용하고 저장' }).click();
  await expect(page).toHaveURL(/view=dashboard/);
  expect(data.saved.workbook?.assets[0]).toMatchObject({ amount: 2345678, weight: null, quantity: null, average: null });
});

test('embedded analysis reloads saved holdings and guards repeat clicks during a write', async ({ page }) => {
  const data = await setup(page);
  let release!: () => void;
  const waiting = new Promise<void>(done => { release = done; });
  await page.route('**/api/portfolio/workbook', async route => {
    if (route.request().method() === 'POST') await waiting;
    await route.fallback();
  });
  await page.goto('/portfolio/?view=dashboard&tab=analysis');
  await saveButton(page).click();
  await expect(saveButton(page)).toBeDisabled();
  await saveButton(page).evaluate(button => { (button as HTMLButtonElement).click(); });
  release();
  await expect(page).toHaveURL(/tab=overview/);
  await expect(page.locator('.holding-edit')).toContainText(asset.name);
  expect(data.writes).toHaveLength(1);
});

for (const mode of ['result', 'failed', 'design-system']) test(`preview ${mode} exercises original-input saving without customer API writes`, async ({ page }) => {
  const calls: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/portfolio/')) calls.push(request.url()); });
  await page.goto(`/portfolio/?preview=${mode}`);
  await saveButton(page).click();
  await page.getByRole('button', { name: '입력한 보유로 교체' }).click();
  await expect(page).toHaveURL(/preview=dashboard/);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('edge-preview-workbook')!));
  expect(saved.workbook.assets).toHaveLength(1);
  expect(saved.workbook.assets[0]).toMatchObject({ amount: 1000000, quantity: null, average: null });
  expect(calls).toEqual([]);
});

test('save controls and confirmation remain reachable with enlarged text on a narrow screen', async ({ page }) => {
  await setup(page, 'failed', source, { version: 1, workbook: source });
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto('/portfolio/');
  await page.addStyleTag({ content: 'html { font-size: 200%; }' });
  await saveButton(page).scrollIntoViewIfNeeded();
  await expect(saveButton(page)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('input-save-narrow.png') });
  await saveButton(page).focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(saveButton(page)).toBeFocused();
});

test('cancelling replacement after entering a percentage total restores focus to the enabled amount action', async ({ page }) => {
  const data = await setup(page, 'failed', { ...source, assets: [{ ...asset, amount: null, weight: 100 }] }, { version: 1, workbook: source });
  await page.goto('/portfolio/');
  await saveButton(page).click();
  await page.getByLabel('총 투자금액 (원)').fill('2000000');
  const submit = page.getByRole('button', { name: '금액 적용하고 저장' });
  await submit.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(submit).toBeFocused();
  await expect(page.getByLabel('총 투자금액 (원)')).toHaveValue('2000000');
  expect(data.writes).toEqual([]);
});

test('a late embedded save cannot reload another tab over an unfinished holding edit', async ({ page }) => {
  const data = await setup(page);
  let release!: () => void;
  const waiting = new Promise<void>(done => { release = done; });
  await page.route('**/api/portfolio/workbook', async route => {
    if (route.request().method() === 'POST') await waiting;
    await route.fallback();
  });
  await page.goto('/portfolio/?view=dashboard&tab=analysis');
  await saveButton(page).click();
  await expect(saveButton(page)).toBeDisabled();
  await page.getByRole('button', { name: '한눈에', exact: true }).click();
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  const editor = page.locator('.asset-editor');
  await editor.locator('select[name="account"]').selectOption('ISA');
  const response = page.waitForResponse(r => r.url().endsWith('/workbook') && r.request().method() === 'POST');
  release(); await response;
  await expect.poll(() => data.writes.length).toBe(1);
  await expect(editor.locator('select[name="account"]')).toHaveValue('ISA');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '수정 취소하기', exact: true }).click();
  await expect(editor).not.toBeVisible();
  await page.getByRole('button', { name: 'AI 포폴 분석', exact: true }).click();
  await page.getByRole('link', { name: '저장한 포트폴리오 보기' }).click();
  await expect(page.locator('.holding-edit')).toContainText(asset.name);
});
