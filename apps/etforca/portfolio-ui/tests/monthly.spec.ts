import { test, expect } from '@playwright/test';

test('monthly amount opens immediately below its option and is visible on a small phone', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await page.route('**/api/portfolio/**', route => route.fulfill({ json: { instruments: [], counts: { KR: 0, US: 0 } } }));
  await page.goto('/portfolio/');
  await page.getByRole('button', { name: '종목부터 추천받기' }).click();
  await page.getByRole('button', { name: '매달 꾸준히', exact: true }).click();
  const input = page.getByRole('textbox', { name: '추가 투자금액' });
  await expect(page.locator('.bar .btn')).toBeDisabled();
  await expect(input).toBeInViewport({ ratio: 1 });
  const option = await page.getByRole('button', { name: '매달 꾸준히', exact: true }).boundingBox();
  const field = await input.boundingBox();
  const next = await page.getByRole('button', { name: '목돈 한 번에', exact: true }).boundingBox();
  expect(field!.y).toBeGreaterThanOrEqual(option!.y + option!.height);
  expect(field!.y + field!.height).toBeLessThanOrEqual(next!.y);
  await input.fill('100');
  await expect(page.locator('.bar .btn')).toBeEnabled();
  await page.getByRole('button', { name: '목돈 한 번에', exact: true }).click();
  await expect(input).toHaveCount(0);
});
