import { test, expect } from '@playwright/test';

const sheet = (page: import('@playwright/test').Page) => page.getByRole('dialog', { name: '내 ETF에 무슨 일이? 핵심만 매일 확인해요.' });

test('first visit and tab changes do not count as a return; second visit offers the app once', async ({ page }, testInfo) => {
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.getByRole('heading', { name: '내 포트폴리오', exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('edge-preview-dashboard-visited'))).toBe('seen');
  await page.getByRole('button', { name: '배당 달력', exact: true }).click();
  await page.waitForTimeout(3500);
  await expect(sheet(page)).not.toBeVisible();
  await page.reload();
  await expect(sheet(page)).toBeVisible({ timeout: 8000 });
  await expect(page.locator('dialog[open]')).toHaveCount(1);
  await expect(sheet(page).getByRole('link', { name: 'App Store에서 무료로 받기' })).toHaveAttribute('href', 'https://apps.apple.com/kr/app/etf-orca/id6816826436');
  await expect(sheet(page).getByRole('img')).toHaveCount(6);
  await expect.poll(() => sheet(page).getByRole('img').first().evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('dashboard-app-download.png') });
  await page.keyboard.press('Escape');
  await expect(sheet(page)).not.toBeVisible();
  await page.getByRole('button', { name: '한눈에', exact: true }).click();
  await page.waitForTimeout(3500);
  await expect(sheet(page)).not.toBeVisible();
  await page.reload();
  await page.waitForTimeout(3500);
  await expect(sheet(page)).not.toBeVisible();
});

test('sheet waits for editing to finish and restores focus on dismissal', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-preview-dashboard-visited', 'seen'));
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  await page.waitForTimeout(3500);
  await expect(sheet(page)).not.toBeVisible();
  await page.keyboard.press('Escape');
  await expect(sheet(page)).toBeVisible({ timeout: 8000 });
  await sheet(page).getByRole('button', { name: '앱 다운로드 안내 닫기' }).click();
  await expect(page.getByRole('button', { name: '종목 추가', exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).not.toBe('hidden');
});

test('design preview shows the actual sheet without changing visit history', async ({ page }, testInfo) => {
  await page.goto('/portfolio/?preview=design-system');
  const launcher = page.getByRole('button', { name: '앱 다운로드 시트 열어보기' });
  await launcher.click();
  await expect(sheet(page)).toBeVisible();
  await expect(sheet(page).getByRole('img').first()).toBeVisible();
  await expect.poll(() => sheet(page).getByRole('img').first().evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('app-download.png') });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await sheet(page).getByRole('button', { name: '나중에 볼게요' }).click();
  await expect(launcher).toBeFocused();
  expect(await page.evaluate(() => localStorage.getItem('edge-preview-dashboard-visited'))).toBeNull();
});

test('blocked browser storage never breaks the dashboard or invents a return visit', async ({ page }) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new DOMException('blocked', 'SecurityError'); };
    Storage.prototype.setItem = () => { throw new DOMException('blocked', 'SecurityError'); };
  });
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.getByRole('heading', { name: '내 포트폴리오', exact: true })).toBeVisible();
  await page.waitForTimeout(3500);
  await expect(sheet(page)).not.toBeVisible();
});

test('narrow screens with enlarged text keep close and download reachable', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.goto('/portfolio/?preview=design-system');
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await page.getByRole('button', { name: '앱 다운로드 시트 열어보기' }).click();
  await expect(sheet(page)).toBeVisible();
  const cta = sheet(page).getByRole('link', { name: 'App Store에서 무료로 받기' });
  await expect(cta).toBeInViewport();
  await expect(sheet(page).getByRole('button', { name: '앱 다운로드 안내 닫기' })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await sheet(page).evaluate(el => getComputedStyle(el).animationName)).toBe('none');
  await page.keyboard.press('Escape');
  await expect(sheet(page)).not.toBeVisible();
});

test('preview reset restores a first visit without clearing real dashboard history', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('edge-preview-dashboard-visited'))).toBe('seen');
  await page.evaluate(() => localStorage.setItem('edge-dashboard-visited', 'seen'));
  await page.reload();
  await expect(sheet(page)).toBeVisible({ timeout: 8000 });
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '테스트 초기화', exact: true }).click();
  await expect(page.getByRole('heading', { name: '내 포트폴리오', exact: true })).toBeVisible();
  await page.waitForTimeout(3500);
  await expect(sheet(page)).not.toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('edge-dashboard-visited'))).toBe('seen');
});

for (const closeWith of ['close', 'later', 'Escape', 'store']) {
  test(`dismissal by ${closeWith} suppresses returns for exactly 24 hours`, async ({ page, context }) => {
    const now = new Date('2026-10-21T12:00:00Z');
    await page.clock.setFixedTime(now);
    await page.addInitScript(() => localStorage.setItem('edge-preview-dashboard-visited', 'seen'));
    await page.goto('/portfolio/?preview=dashboard');
    await expect(sheet(page)).toBeVisible({ timeout: 8000 });
    if (closeWith === 'Escape') await page.keyboard.press('Escape');
    else if (closeWith === 'store') {
      await context.route('https://apps.apple.com/**', route => route.fulfill({ body: '' }));
      const popup = page.waitForEvent('popup');
      await sheet(page).getByRole('link', { name: 'App Store에서 무료로 받기' }).click();
      await (await popup).close();
    } else await sheet(page).getByRole('button', { name: closeWith === 'close' ? '앱 다운로드 안내 닫기' : '나중에 볼게요' }).click();
    await expect(sheet(page)).not.toBeVisible();
    const until = now.getTime() + 86400000;
    expect(await page.evaluate(() => localStorage.getItem('edge-preview-app-dismissed-until'))).toBe(String(until));
    await page.clock.setFixedTime(until - 1);
    await page.reload();
    await page.waitForTimeout(3500);
    await expect(sheet(page)).not.toBeVisible();
    await page.clock.setFixedTime(until);
    await page.reload();
    await expect(sheet(page)).toBeVisible({ timeout: 8000 });
  });
}
