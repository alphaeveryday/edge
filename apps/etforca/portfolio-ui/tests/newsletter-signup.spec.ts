import { test, expect } from '@playwright/test';

// 8차 결과 틀에서 결과 화면의 뉴스레터 신청은 빠졌다. 같은 신청 부품은 대시보드 안내와 전시 화면에 남으므로,
// 실제 예시 메일 그림과 좁은 화면·큰 글자에서의 신청 가능 여부를 전시 화면에서 확인한다(미리보기는 고객 API 를 쓰지 않는다).
test('the daily email offer shows its real template preview and can be submitted at 320px with enlarged text', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/portfolio/')) writes.push(request.url()); });
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto('/portfolio/?preview=design-system');
  const signup = page.locator('.newsletter-signup');
  const image = signup.locator('.newsletter-example img');
  await image.scrollIntoViewIfNeeded();
  await expect.poll(() => image.evaluate((e: HTMLImageElement) => e.naturalWidth)).toBe(680);
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await signup.getByLabel('뉴스레터 이메일').fill('preview@example.test');
  await signup.getByRole('button', { name: '이메일로 받아보기' }).click();
  await expect(signup.getByRole('status')).toContainText('확인 메일');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(writes).toEqual([]);
});
