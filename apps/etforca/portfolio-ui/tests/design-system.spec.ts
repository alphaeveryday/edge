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
  await expect(page.locator('.ds-spinner')).toHaveCSS('animation-name', 'none');
});

test('search affordances and dividend data remain visible after token changes', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await expect(page.locator('.find__plus').first()).toHaveCSS('color', 'rgb(255, 255, 255)');
  await page.goto('/portfolio/?preview=dashboard');
  const month = page.getByRole('button', { name: /^10월 실수령/ });
  await expect(month.locator('b')).toHaveCSS('background-color', 'rgb(27, 100, 218)');
  await expect(month.locator('i')).toHaveCSS('box-shadow', 'rgb(27, 100, 218) 0px 0px 0px 1px inset');
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
  await expect(page.locator('.asset-row')).toHaveCount(6);
  await page.locator('.asset-row').first().click();
  await page.getByRole('button', { name: '종목 삭제', exact: true }).click();
  await expect(page.locator('.ds-confirm-dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '종목 삭제', exact: true })).toBeFocused();
  await expect(page.locator('.asset-row')).toHaveCount(6);
  await page.getByRole('button', { name: '종목 삭제', exact: true }).click();
  await page.getByRole('button', { name: '삭제하기', exact: true }).click();
  await expect(page.locator('.asset-row')).toHaveCount(5);
  await page.reload();
  await expect(page.locator('.asset-row')).toHaveCount(5);
});

test('replacing a saved portfolio keeps the loss-of-records confirmation in the shared dialog', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.locator('.asset-row').first().click();
  await page.getByLabel('종목명', { exact: true }).fill('내가 수정한 ETF');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.goto('/portfolio/?preview=result');
  const save = page.locator('.feedback-anchor>.save-portfolio>button');
  await save.click();
  await expect(page.locator('.ds-confirm-dialog[open]')).toContainText('기존 배당·수익 기록도 함께 바뀌어요');
  await page.getByRole('button', { name: '취소', exact: true }).click();
  expect(await page.evaluate(() => localStorage.getItem('edge-preview-workbook'))).toContain('내가 수정한 ETF');
  await expect(save).toBeFocused();
  await save.click();
  await page.getByRole('button', { name: '바꾸고 저장하기' }).click();
  await expect(page).toHaveURL(/preview=dashboard/);
  await expect(page.locator('.asset-list')).not.toContainText('내가 수정한 ETF');
});

test('a duplicate month needs confirmation before replacing the saved financial record', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  await page.getByLabel('월', { exact: true }).fill('2026-10');
  await page.getByLabel('월말 총자산', { exact: true }).fill('30000000');
  await page.getByRole('button', { name: '월 기록 저장' }).click();
  await expect(page.getByRole('dialog')).toContainText('2026-10 수익 기록을 바꿀까요');
  await page.getByRole('button', { name: '취소', exact: true }).click();
  await expect(page.locator('.return-records')).toContainText('25,586,400원');
  await page.getByRole('button', { name: '월 기록 저장' }).click();
  await page.getByRole('button', { name: '기록 바꾸기', exact: true }).click();
  await expect(page.locator('.return-records')).toContainText('30,000,000원');
  await expect(page.locator('.return-records')).not.toContainText('25,586,400원');
});

test('a slow monthly overwrite restores focus after failure and after a successful retry', async ({ page }) => {
  let fail = true;
  let saved = { version: 1, workbook: { title: '내 포트폴리오', fx: null, assets: [], dividends: [], months: [{ month: '2026-10', value: 1000000, deposit: 0, withdrawal: 0, return_pct: null }] } };
  await page.route('**/api/portfolio/**', async route => {
    if (!new URL(route.request().url()).pathname.endsWith('/workbook')) return route.fulfill({ json: {} });
    if (route.request().method() === 'POST') {
      await new Promise(resolve => setTimeout(resolve, 500));
      if (fail) return route.fulfill({ status: 503, json: {} });
      saved = { version: 2, workbook: route.request().postDataJSON().workbook };
    }
    return route.fulfill({ json: saved });
  });
  await page.goto('/portfolio/?view=dashboard');
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  await page.getByLabel('월', { exact: true }).fill('2026-10');
  await page.getByLabel('월말 총자산', { exact: true }).fill('2000000');
  const save = page.getByRole('button', { name: '월 기록 저장' });
  await save.click();
  await page.getByRole('button', { name: '기록 바꾸기', exact: true }).click();
  await expect(page.locator('.dashboard-error')).toBeVisible();
  await expect(save).toBeFocused();
  await expect(page.locator('.return-records')).toContainText('1,000,000원');
  fail = false;
  await save.click();
  await page.getByRole('button', { name: '기록 바꾸기', exact: true }).click();
  await expect(page.locator('.return-records')).toContainText('2,000,000원');
  await expect(save).toBeFocused();
});
