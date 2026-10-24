import { test, expect } from '@playwright/test';
import { addCash, addHolding } from './holdings';

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

// 막힌 '다음' 아래에는 지금 무엇을 해야 넘어가는지 한 줄로 적는다(버튼만 회색이면 왜 막혔는지 모른다).
test('input guidance describes the current decision on each step', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await page.getByLabel('현금 비중').fill('100');
  await addHolding(page, 'VOO', 'Vanguard S&P 500 ETF');
  await expect(page.locator('#step-guidance')).toHaveText('각 종목의 비중을 입력해 주세요.');
  await page.getByLabel('Vanguard S&P 500 ETF 비중', { exact: true }).fill('1');
  await expect(page.locator('#step-guidance')).toHaveText('비중 합계가 100%가 되면 넘어갈 수 있어요. 지금 101%예요.');
  await page.getByRole('button', { name: 'Vanguard S&P 500 ETF 빼기' }).click();
  // 비중만으로는 사고팔 금액을 못 정한다 — 전체 금액이 없는 분석은 없다.
  await page.locator('#total').fill('');
  await expect(page.locator('#step-guidance')).toHaveText('전체 금액을 적으면 넘어갈 수 있어요');
  await page.locator('#total').fill('100');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.locator('#step-guidance')).toHaveText('매달 넣을 돈을 골라 주세요');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '직접', exact: true }).click();
  await expect(page.locator('#step-guidance')).toHaveText('0보다 큰 금액을 적어 주세요');
  await page.getByLabel('매달 넣을 돈(만 원)').fill('100');
  await expect(page.locator('#step-guidance')).toHaveText('언제쯤 쓸지 골라 주세요');
  await page.getByRole('button', { name: /^정한 때 없어요/ }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByRole('button', { name: '분석 시작하기' })).toBeEnabled();
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

// 행동 버튼은 화면에 붙어 있되(sticky), 맨 아래까지 내려도 입력 내용을 덮지 않는다.
// 모바일은 아래 고정 막대라 폼 끝이 막대 위에서 끝나야 하고, PC 는 옆 열(입력 요약 패널)이라 폼과 가로로 겹치지 않아야 한다.
test('the action stays reachable and never covers the end of the form', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator('.fl-cta')).toHaveCSS('position', 'sticky');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeInViewport();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  const form = await page.locator('.fl-step').boundingBox();
  const bar = await page.locator('.fl-cta').boundingBox();
  const beside = bar!.x >= form!.x + form!.width - 1;
  if (!beside) expect(form!.y + form!.height).toBeLessThanOrEqual(bar!.y + 1);
  expect(beside).toBe((page.viewportSize()?.width ?? 0) >= 1080);
});

test('a wide performance chart stays readable and pointer selection tracks the same data after resizing', async ({ page }) => {
  // 결과 화면은 8차 지난 기간 칸으로 바뀌었고 백테스트는 없앴다. 같은 성과 그래프(PerformanceChart)는 대시보드 수익 기록에 남는다.
  await page.goto('/portfolio/?preview=dashboard');
  await page.getByRole('button', { name: '수익 기록', exact: true }).click();
  const chart = page.locator('.report-history svg');
  const box = await chart.boundingBox();
  expect(box!.height).toBeLessThanOrEqual(301);
  await chart.hover({ position: { x: box!.width - 18, y: 60 } });
  await expect(page.locator('.history-date')).toHaveText('2026-10-18');
  await page.setViewportSize({ width: 320, height: 900 });
  await expect.poll(async () => chart.evaluate((el: SVGSVGElement) => Math.abs(el.viewBox.baseVal.width - el.getBoundingClientRect().width))).toBeLessThan(2);
  await chart.hover({ position: { x: 0, y: 60 } });
  await expect(page.locator('.history-date')).toHaveText('2026-10-16');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
