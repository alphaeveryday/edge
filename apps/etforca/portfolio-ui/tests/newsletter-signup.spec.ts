import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // The separate feedback tests cover its automatic popup; here the reader already dismissed it.
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-feedback:preview', 'done'));
});

test('the daily email offer shows its real template preview and a sourced quote', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/portfolio/')) writes.push(request.url()); });
  await page.goto('/portfolio/?preview=result');
  const button = page.locator('.report-premium');
  await expect(button).toHaveText('내 ETF 핵심 소식 이메일로 받기');
  await button.click();
  const dialog = page.getByRole('dialog', { name: '내 ETF 핵심 소식 이메일로 받기', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog).not.toContainText('1일 이내');
  const image = dialog.locator('.newsletter-example img');
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((e: HTMLImageElement) => e.naturalWidth)).toBe(680);
  await expect(dialog.getByRole('link', { name: /워런 버핏/ })).toHaveAttribute('href', 'https://www.berkshirehathaway.com/2013ar/2013ar.pdf#page=20');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: `test-results/newsletter-offer-${test.info().project.name}.png` });
  await dialog.getByLabel('뉴스레터 이메일').fill('preview@example.test');
  await dialog.getByRole('button', { name: '매일 핵심 소식 받기' }).click();
  await expect(dialog.getByRole('status')).toContainText('확인 메일');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(button).toBeFocused();
  expect(writes).toEqual([]);
});

test('email signup remains reachable at 320px with enlarged text', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto('/portfolio/?preview=result');
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await page.locator('.report-premium').click();
  const dialog = page.getByRole('dialog', { name: '내 ETF 핵심 소식 이메일로 받기', exact: true });
  await dialog.getByLabel('뉴스레터 이메일').fill('preview@example.test');
  await dialog.getByRole('button', { name: '매일 핵심 소식 받기' }).click();
  await expect(dialog.getByRole('status')).toContainText('확인 메일');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
