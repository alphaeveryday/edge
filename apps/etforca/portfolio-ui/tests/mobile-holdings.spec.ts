import { test, expect } from '@playwright/test';

test('holdings keep the mobile summary short without losing details or editing', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  const row = page.locator('.holdings-table tbody tr').first();
  await expect(row).toBeVisible();
  const mobile = page.viewportSize()!.width <= 600;
  const toggle = row.getByRole('button', { name: /상세 정보/ });
  if (mobile) {
    await expect(row.locator('td:visible')).toHaveCount(4);
    await expect(row.locator('[data-label=현재가]')).toBeHidden();
    expect((await row.boundingBox())!.height).toBeLessThan(320);
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(toggle).toHaveAccessibleName(/접기$/);
    await expect(row.locator('td:visible')).toHaveCount(11);
    await toggle.press('Enter');
    await expect(row.locator('td:visible')).toHaveCount(4);
  } else {
    await expect(toggle).toBeHidden();
    await expect(row.locator('td:visible')).toHaveCount(11);
  }
  await row.screenshot({ path: `test-results/holdings-summary-${test.info().project.name}.png` });
  await row.locator('.holding-edit').click();
  await expect(page.getByRole('dialog')).toBeVisible();
});

for (const month of ['01', '06', '10', '12']) test(`dividend report opens with current month ${month} unobscured by the sticky name`, async ({ page }) => {
  await page.clock.setFixedTime(new Date(`2026-${month}-15T03:00:00Z`));
  await page.route('**/api/portfolio/**', route => route.fulfill({ json: route.request().url().endsWith('/workbook')
    ? { version: 1, workbook: { title: '내 포트폴리오', fx: null, assets: [], months: [], dividends: [] } }
    : { market: null, days: [], instruments: [] } }));
  await page.goto('/portfolio/?view=dashboard&tab=dividends');
  const report = page.locator('.dividend-report');
  const scroller = report.locator('.dividend-table-scroll');
  const current = report.locator('thead th').nth(Number(month));
  await expect(current).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const assertCurrentVisible = () => expect(async () => {
    const name = (await report.locator('thead th').first().boundingBox())!;
    const cell = (await current.boundingBox())!;
    const container = (await scroller.boundingBox())!;
    expect(cell.x).toBeGreaterThanOrEqual(name.x + name.width - 1);
    expect(cell.x + cell.width).toBeLessThanOrEqual(container.x + container.width + 1);
    const bar = (await report.locator('.dividend-bars button[aria-pressed=true]').boundingBox())!;
    const plot = (await report.locator('.dividend-scroll').boundingBox())!;
    expect(bar.x).toBeGreaterThanOrEqual(plot.x - 1);
    expect(bar.x + bar.width).toBeLessThanOrEqual(plot.x + plot.width + 1);
  }).toPass({ timeout: 5000 });
  await assertCurrentVisible();
  await page.setViewportSize({ width: 320, height: 900 });
  await assertCurrentVisible();
  await report.getByRole('button', { name: '실수령 기록', exact: true }).click();
  await assertCurrentVisible();
  await report.getByLabel('연도').selectOption('2027');
  expect(await scroller.evaluate(e => e.scrollLeft)).toBe(0);
  await report.getByLabel('연도').selectOption('2026');
  await assertCurrentVisible();
  if (month === '10') await report.screenshot({ path: `test-results/dividend-current-${test.info().project.name}.png` });
});

test('compact holdings and the current month fit a 320px screen with enlarged text', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto('/portfolio/?preview=dashboard');
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await page.locator('.holding-details').first().click();
  await expect(page.locator('.holdings-table tbody tr').first().locator('[data-label=현재가]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  const report = page.locator('.dividend-report');
  const name = (await report.locator('thead th').first().boundingBox())!;
  const current = (await report.locator('thead th').nth(10).boundingBox())!;
  expect(current.x).toBeGreaterThanOrEqual(name.x + name.width - 1);
  expect(current.x).toBeLessThan(320);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('populated dividend rows open at this month and still allow browsing earlier months', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard&tab=dividends');
  const report = page.locator('.dividend-report');
  await expect(report.locator('tbody tr')).toHaveCount(5);
  await page.evaluate(() => document.fonts.ready);
  const current = report.locator('thead th').nth(10);
  const scroller = report.locator('.dividend-table-scroll');
  const cell = (await current.boundingBox())!;
  const name = (await report.locator('thead th').first().boundingBox())!;
  const container = (await scroller.boundingBox())!;
  expect(cell.x).toBeGreaterThanOrEqual(name.x + name.width - 1);
  expect(cell.x + cell.width).toBeLessThanOrEqual(container.x + container.width + 1);
  const selectedBar = (await report.locator('.dividend-bars button[aria-pressed=true]').boundingBox())!;
  const plot = (await report.locator('.dividend-scroll').boundingBox())!;
  expect(selectedBar.x).toBeGreaterThanOrEqual(plot.x - 1);
  expect(selectedBar.x + selectedBar.width).toBeLessThanOrEqual(plot.x + plot.width + 1);
  await report.screenshot({ path: `test-results/dividend-populated-${test.info().project.name}.png` });
  await scroller.hover();
  await page.mouse.wheel(-2000, 0);
  await expect.poll(() => scroller.evaluate(e => e.scrollLeft)).toBe(0);
  await report.getByRole('button', { name: '실수령 기록', exact: true }).click();
  // Switching series must not take the user away from a month they browsed to.
  await expect.poll(() => scroller.evaluate(e => e.scrollLeft)).toBe(0);
});
