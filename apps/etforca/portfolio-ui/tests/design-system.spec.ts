import { test, expect } from '@playwright/test';

test('the design catalog uses production controls without customer traffic or persistence', async ({ page }) => {
  const calls: string[] = [];
  page.on('request', request => { if (/\/api\/portfolio|posthog\.com/.test(request.url())) calls.push(request.url()); });
  await page.goto('/portfolio/?preview=design-system');
  await expect(page.getByRole('heading', { name: /편안한 금융 경험/ })).toBeVisible();
  await expect(page.getByRole('button', { name: '저장 중' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '저장 중' })).toHaveAttribute('aria-busy', 'true');
  await expect(page.getByLabel('이메일 주소', { exact: true })).toHaveCSS('border-color', 'rgb(126, 135, 148)');
  await expect(page.locator('#ds-error')).toHaveCSS('border-color', 'rgb(210, 32, 48)');
  await page.getByLabel('이메일 주소', { exact: true }).fill('design@example.test');
  await page.getByRole('button', { name: '예시 입력 확인하기' }).click();
  await expect(page.getByRole('status').last()).toContainText('포트폴리오를 저장했어요');
  await expect(page.locator('form [role=status]')).toContainText('저장하거나 메일을 보내지 않았어요');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await expect(page.locator('.ds-example-row')).toContainText('26.9%');
  await page.getByRole('button', { name: '팝업 열어보기' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('button', { name: '팝업 열어보기' })).toBeFocused();
  await page.getByRole('button', { name: '팝업 열어보기' }).click();
  await page.getByRole('button', { name: '확인', exact: true }).click();
  await expect(page.getByRole('button', { name: '팝업 열어보기' })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(calls).toEqual([]);
});

test('tokens stay consistent across input, dashboard and catalog, including text enlargement', async ({ page }) => {
  for (const screen of ['input', 'dashboard', 'design-system']) {
    await page.goto('/portfolio/?preview=' + screen);
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--color-primary').trim())).toBe('#1b64da');
    const amounts = screen === 'dashboard' ? await page.locator('.dashboard-stats strong').evaluateAll(nodes => nodes.map(node => parseFloat(getComputedStyle(node).fontSize))) : [];
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    if (screen === 'dashboard') {
      const enlarged = await page.locator('.dashboard-stats strong').evaluateAll(nodes => nodes.map(node => parseFloat(getComputedStyle(node).fontSize)));
      expect(enlarged.every((size, i) => size > amounts[i])).toBe(true);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), screen).toBe(true);
  }
  for (const spinner of await page.locator('.ds-spinner').all()) await expect(spinner).toHaveCSS('animation-name', 'none');
});

test('search affordances and dividend data remain visible after token changes', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await expect(page.locator('.find__plus').first()).toHaveCSS('color', 'rgb(255, 255, 255)');
  await page.goto('/portfolio/?preview=dashboard');
  const month = page.getByRole('button', { name: /^2026-10 예상/ });
  await expect(month.locator('b')).toHaveCSS('background-color', 'rgb(32, 105, 133)');
  await expect(month.locator('i')).toHaveCSS('background-color', 'rgb(0, 119, 128)');
  await expect(month.locator('i')).toHaveCSS('box-shadow', 'none');
});

test('keyboard focus stays distinct and the final CTA grows with enlarged text', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  const search = page.locator('.find .field').first();
  await search.focus();
  await expect(search).toHaveCSS('outline-color', 'rgb(27, 100, 218)');
  await expect(search).toHaveCSS('outline-width', '3px');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '이미 다 매수한 상태에요', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '10년 넘게 뒤', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  const submit = page.getByRole('button', { name: '내 포트폴리오 분석하기', exact: true });
  await expect(submit).toBeVisible();
  expect(await submit.evaluate(el => {
    const range = document.createRange(); range.selectNodeContents(el);
    const text = range.getBoundingClientRect(), button = el.getBoundingClientRect();
    return text.top >= button.top && text.bottom <= button.bottom && text.left >= button.left && text.right <= button.right;
  })).toBe(true);
  await expect.poll(() => page.evaluate(() => {
    const bar = document.querySelector('.bar')!;
    return getComputedStyle(bar).position === 'static' || parseFloat(getComputedStyle(document.querySelector('.app')!).paddingBottom) >= bar.getBoundingClientRect().height;
  })).toBe(true);
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  expect(await page.evaluate(() => document.querySelector('.step.in')!.getBoundingClientRect().bottom <= document.querySelector('.bar')!.getBoundingClientRect().top)).toBe(true);
});

test('destructive confirmation can cancel without losing assets and confirms only the chosen deletion', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.locator('.holding-edit')).toHaveCount(6);
  await page.locator('.holding-edit').first().click();
  await page.getByRole('button', { name: '종목 삭제', exact: true }).click();
  await expect(page.locator('.ds-confirm-dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '종목 삭제', exact: true })).toBeFocused();
  await expect(page.locator('.holding-edit')).toHaveCount(6);
  await page.getByRole('button', { name: '종목 삭제', exact: true }).click();
  await page.getByRole('button', { name: '삭제하기', exact: true }).click();
  await expect(page.locator('.holding-edit')).toHaveCount(5);
  await page.reload();
  await expect(page.locator('.holding-edit')).toHaveCount(5);
});

test('saving the entered portfolio replaces holdings directly without a second confirmation', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.locator('.holding-edit').filter({ hasText: '미국시장 ETF 예시' }).click();
  await page.getByLabel('보유 수량', { exact: true }).fill('999');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.goto('/portfolio/?preview=result');
  const save = page.locator('.feedback-anchor .save-portfolio button').first();
  await save.click();
  await expect(page).toHaveURL(/preview=dashboard/);
  await page.locator('.holding-edit').filter({ hasText: '미국시장 ETF 예시' }).click();
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('400');
});
