import { test, expect } from '@playwright/test';
import { addHolding } from './holdings';

// 바구니는 계좌별로 묶어 보이고(같은 종목도 계좌가 다르면 따로), 어느 폭에서도 가로로 넘치지 않는다.
for (const width of [320, 390, 1280]) test(`the basket groups holdings by account without horizontal overflow at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  await page.goto('/portfolio/?preview=input');
  await addHolding(page, '069500', 'KODEX 200', '100');
  await addHolding(page, '069500', 'KODEX 200', undefined, 'ISA');
  await expect(page.locator('.fl-group')).toHaveCount(2);
  await expect(page.getByLabel('KODEX 200 금액', { exact: true }).first()).toHaveValue('100');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: `test-results/input-details-${width}-${test.info().project.name}.png`, fullPage: true });
});

for (const state of ['queued', 'running']) test(`the analysis role diagram shows the real stage: ${state}`, async ({ page }) => {
  await page.goto(`/portfolio/?preview=${state}`);
  const diagram = page.getByRole('list', { name: '분석 역할과 검토 흐름' });
  for (const name of ['종목 분석 AI', '산업 분석 AI', '경제 분석 AI', '포트폴리오 매니저', '리스크 매니저']) await expect(diagram.getByText(name, { exact: true })).toBeVisible();
  await expect(diagram.locator('.fl-hand')).toHaveCount(2);
  await expect(diagram.locator('[data-s="run"]')).toHaveCount(state === 'running' ? 2 : 0);
  // 한 AI가 역할을 차례로 본다 — 동시에 도는 다섯 에이전트처럼 보이지 않게 적는다.
  await expect(page.getByText('AI 하나가 다섯 역할을 차례로 맡아 검토해요')).toBeVisible();
  // 화면 낭독기에는 단계가 바뀔 때만 알린다(경과 초는 알리지 않는다).
  await expect(page.locator('.fl-sr[role=status]')).toHaveText(state === 'running' ? '종목·산업 분석 중' : '순서를 기다리는 중');
  if (state === 'queued') await expect(page.getByRole('heading', { name: '순서를 기다리고 있어요' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: `test-results/analysis-roles-${state}-${test.info().project.name}.png`, fullPage: true });
});

test('losing the connection while waiting says so and keeps the wait screen', async ({ page }) => {
  let online = true;
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'waiting'));
  await page.route('**/api/portfolio/analyses/waiting', route => online ? route.fulfill({ json: { id: 'waiting', state: 'queued', input: { holdings: [], horizon: '3년 안', question: '' } } }) : route.abort());
  await page.goto('/portfolio/');
  await expect(page.getByRole('heading', { name: '순서를 기다리고 있어요' })).toBeVisible();
  online = false;
  await expect(page.getByText(/연결이 끊겼어요/)).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole('heading', { name: '순서를 기다리고 있어요' })).toBeVisible();
  online = true;
  await expect(page.getByText(/연결이 끊겼어요/)).toHaveCount(0, { timeout: 15000 });
});
