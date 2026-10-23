import { test, expect } from '@playwright/test';

// 매달 넣을 돈은 결과의 '앞으로 넣을 돈' 칸을 정한다. 0원 월 납입은 서버가 거절하므로 화면이 먼저 막는다.
test('monthly amount: presets, −/+ and direct entry; zero is refused before the analysis', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  let saved: any;
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().includes('/drafts/')) saved = route.request().postDataJSON();
    return route.fulfill({ json: { instruments: [], counts: { KR: 0, US: 0 } } });
  });
  await page.goto('/portfolio/');
  await page.getByRole('button', { name: '아직 투자 전이에요' }).click();
  await expect(page.getByText('처음 시작해요')).toBeVisible();
  await expect(page.getByRole('heading', { name: '매달 넣을 돈' })).toBeVisible();
  const amount = page.getByRole('textbox', { name: '매달 넣을 돈(만 원)' });
  await page.getByRole('button', { name: '직접', exact: true }).click();
  await expect(amount).toBeFocused();
  await expect(amount).toBeInViewport({ ratio: 1 });
  await expect(page.getByText('0보다 큰 금액을 적어 주세요').first()).toBeVisible();
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
  await amount.fill('25');
  await page.getByRole('button', { name: '10만 원 늘리기' }).click();
  await expect(amount).toHaveValue('35');
  await page.getByRole('button', { name: '10만 원 줄이기' }).click();
  await expect(amount).toHaveValue('25');
  // 1.5만 원(15,000원)은 그대로 — 점을 지워 15만 원으로 키우지 않는다.
  await amount.fill('1.5');
  await expect(amount).toHaveValue('1.5');
  // '.5'처럼 서버가 읽지 못하는 꼴은 숫자로 고쳐 보낸다.
  await amount.fill('.5');
  await page.getByRole('button', { name: /^10년 넘게 뒤/ }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect.poll(() => saved?.preferences.contribution).toBe('매달 꾸준히 0.5만 원');
  await page.getByRole('button', { name: '앞 단계로' }).click();
  await page.getByRole('button', { name: '50만', exact: true }).click();
  await expect(page.getByRole('button', { name: '50만', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(amount).toHaveValue('50');
  await page.getByRole('button', { name: /^10년 넘게 뒤/ }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect.poll(() => saved?.preferences.contribution).toBe('매달 꾸준히 50만 원');
  // 투자 전인 사람의 '아직 몰라요'는 서버가 아는 '아직 고민 중'으로 보낸다.
  await page.getByRole('button', { name: '앞 단계로' }).click();
  await page.getByRole('button', { name: '아직 몰라요', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect.poll(() => saved?.preferences.contribution).toBe('아직 고민 중');
  expect(saved.planning).toBe(true);
});
