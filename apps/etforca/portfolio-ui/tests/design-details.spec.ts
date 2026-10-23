import { test, expect } from '@playwright/test';
import { addCash } from './holdings';

test('Korean text and financial figures use the bundled font without a font CDN', async ({ page }) => {
  const externalFonts: string[] = [];
  page.on('request', r => { if (/fonts\.(googleapis|gstatic)\.com/.test(r.url())) externalFonts.push(r.url()); });
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.locator('.holding-edit').first()).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => [...document.fonts].some(f => f.family.replace(/["']/g, '') === 'Pretendard Variable' && f.status === 'loaded'))).toBe(true);
  await expect(page.locator('.dashboard-stats strong').first()).toHaveCSS('font-variant-numeric', 'tabular-nums');
  expect(externalFonts).toEqual([]);
});

test('financial column headings align with their values and chart headings share a baseline', async ({ page, isMobile }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await expect(page.locator('.holding-edit').first()).toBeVisible();
  if (!isMobile && page.viewportSize()!.width > 1000) {
    const geometry = await page.evaluate(() => {
      const headers = [...document.querySelectorAll('.holdings-table thead th')].map(e => e.getBoundingClientRect());
      const cells = [...document.querySelector('.holdings-table tbody tr')!.children].map(e => e.getBoundingClientRect());
      const titles = [...document.querySelectorAll('.dashboard-overview h2')].map(e => e.getBoundingClientRect());
      return [Math.abs(headers[0].left - cells[0].left), Math.abs(headers[1].right - cells[1].right), Math.abs(headers[2].right - cells[2].right), Math.abs(titles[0].top - titles[1].top)];
    });
    expect(geometry.every(gap => gap <= 1)).toBe(true);
  }
  await expect(page.locator('.holdings-table tbody tr').filter({ hasText: '미국시장 ETF 예시' }).locator('[data-label=평가손익]')).toContainText('+877,000원');
  await expect(page.getByRole('button', { name: /^2026-10 예상/ })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: /^2027-09 예상/ }).click();
  await expect(page.getByRole('button', { name: /^2027-09 예상/ }).first()).toHaveAttribute('aria-pressed', 'true');
});

test('input guidance describes the current decision and the optional selection limit is visible', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  await page.getByRole('button', { name: '비율', exact: true }).click();
  await page.getByLabel('현금 비율').fill('100');
  await page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' }).fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard S&P 500 ETF' }).click();
  await expect(page.locator('#step-guidance')).toHaveText('각 종목의 비중을 입력해 주세요.');
  await page.getByLabel('Vanguard S&P 500 ETF 비율', { exact: true }).fill('1');
  await expect(page.locator('#step-guidance')).toHaveText('비중 합계를 100%로 맞춰 주세요. 지금 101%예요.');
  await page.getByRole('button', { name: 'Vanguard S&P 500 ETF 빼기' }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.locator('#step-guidance')).toHaveText('투자 방법을 골라 주세요.');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '매달 꾸준히', exact: true }).click();
  await expect(page.locator('#step-guidance')).toHaveText('매달 투자할 금액을 입력해 주세요.');
  await page.getByLabel('추가 투자금액').fill('100');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.locator('#step-guidance')).toHaveText('투자 기간을 골라 주세요.');
  await page.getByRole('button', { name: '정한 때 없어요' }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeEnabled();
  const choices = page.locator('.q .chip[aria-pressed]');
  for (let i = 0; i < 10; i++) await choices.nth(i).click();
  await expect(page.getByRole('status')).toContainText('10/10개 선택');
  await expect(choices.nth(10)).toBeDisabled();
  await choices.first().click();
  await expect(choices.nth(10)).toBeEnabled();
});

test('adding and editing a holding have distinct titles and a saved result', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '종목 추가', exact: true }).click();
  await expect(page.getByRole('heading', { name: '보유 자산 추가' })).toBeVisible();
  await page.getByRole('dialog').getByRole('combobox', { name: /종목 찾기/ }).fill('069500');
  await page.getByRole('option', { name: /KODEX 200/ }).click();
  await page.getByLabel('보유 수량', { exact: true }).fill('2');
  await page.getByRole('button', { name: '저장', exact: true }).last().click();
  await expect(page.locator('.dashboard-save-status')).toContainText('저장했어요');
  await page.locator('.holding-edit').filter({ hasText: 'KODEX 200' }).click();
  await expect(page.getByRole('heading', { name: '보유 자산 수정' })).toBeVisible();
});

test('the desktop action follows the form while the mobile action leaves scroll space', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await expect(page.locator('.holdings-layout')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  if (page.viewportSize()!.width >= 1100) {
    expect(await page.evaluate(() => document.querySelector('.bar')!.getBoundingClientRect().top >= document.querySelector('.step')!.getBoundingClientRect().bottom)).toBe(true);
  } else {
    await expect(page.locator('.bar')).toHaveCSS('position', 'fixed');
    await page.locator('.askbox').scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const form = await page.locator('.step').boundingBox();
    const bar = await page.locator('.bar').boundingBox();
    expect(form!.y + form!.height).toBeLessThanOrEqual(bar!.y);
  }
});

test('a wide performance chart stays readable and pointer selection tracks the same data after resizing', async ({ page }) => {
  // 결과 화면은 8차 지난 기간 칸으로 바뀌었고, 같은 성과 그래프(PerformanceChart)는 대시보드 백테스트에 남는다.
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '백테스트', exact: true }).click();
  await page.getByRole('button', { name: '내 보유 종목으로 시작' }).click();
  await page.getByRole('button', { name: '과거 수익률 조회', exact: true }).click();
  const chart = page.locator('.report-history svg');
  const box = await chart.boundingBox();
  expect(box!.height).toBeLessThanOrEqual(301);
  await chart.hover({ position: { x: box!.width - 18, y: 60 } });
  await expect(page.locator('.history-date')).toHaveText('2026-10-01');
  await page.setViewportSize({ width: 320, height: 900 });
  await expect.poll(async () => chart.evaluate((el: SVGSVGElement) => Math.abs(el.viewBox.baseVal.width - el.getBoundingClientRect().width))).toBeLessThan(2);
  await chart.hover({ position: { x: 0, y: 60 } });
  await expect(page.locator('.history-date')).toHaveText('2024-11-01');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
