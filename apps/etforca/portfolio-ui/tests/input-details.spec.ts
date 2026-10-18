import { test, expect } from '@playwright/test';

for (const width of [320, 390, 1280]) test(`holdings input groups accounts and shows the portfolio first on mobile at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  await page.goto('/portfolio/?preview=input');
  await page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기', exact: true }).fill('069500');
  await page.getByRole('option', { name: /KODEX 200/ }).click();
  await page.getByLabel('KODEX 200 금액', { exact: true }).fill('100');
  await page.getByRole('button', { name: '+ISA', exact: true }).click();
  const preview = await page.locator('.portfolio-preview').boundingBox();
  const account = await page.locator('.acct').first().boundingBox();
  if (width < 1100) expect(preview!.y + preview!.height).toBeLessThan(account!.y);
  else expect(preview!.x).toBeGreaterThan(account!.x + account!.width);
  await expect(page.locator('.holdings-input .acct')).toHaveCount(2);
  await expect(page.getByLabel('KODEX 200 금액', { exact: true })).toHaveValue('100');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: `test-results/input-details-${width}-${test.info().project.name}.png`, fullPage: true });
});

for (const state of ['queued', 'running']) test(`the analysis role diagram survives the visual redesign: ${state}`, async ({ page }) => {
  await page.goto(`/portfolio/?preview=${state}`);
  const diagram = page.getByRole('list', { name: '분석 역할과 검토 흐름' });
  for (const name of ['종목 분석 AI', '산업 분석 AI', '경제 분석 AI', '포트폴리오 매니저', '리스크 매니저']) await expect(diagram.getByText(name, { exact: true })).toBeVisible();
  await expect(diagram.locator('.hand svg')).toHaveCount(2);
  await expect(diagram.locator('[data-s="run"]')).toHaveCount(state === 'running' ? 2 : 0);
  if (state === 'running') expect(await diagram.locator('[data-s="run"]').first().evaluate(el => getComputedStyle(el, '::before').animationName)).toBe('none');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: `test-results/analysis-roles-${state}-${test.info().project.name}.png`, fullPage: true });
});
