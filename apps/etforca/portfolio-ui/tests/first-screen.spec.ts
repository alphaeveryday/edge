import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const catalog = JSON.parse(readFileSync(new URL('../../portfolio-worker/src/edge_portfolio/data/etf_catalog.json', import.meta.url), 'utf8'));

// 운영과 같은 화면(미리보기 막대 없음)에서 API 만 흉내 낸다.
async function open(page: Page, saved: unknown = null) {
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/workbook')) return route.fulfill({ json: { version: saved ? 1 : 0, workbook: saved } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  return page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' });
}

async function aboveBar(page: Page, element: ReturnType<Page['locator']>) {
  const box = (await element.boundingBox())!, bar = (await page.locator('.bar').boundingBox())!;
  return box.y >= 0 && box.y + box.height <= bar.y;
}

// 입력 중단 분석의 1순위 가설: 첫 행동(종목 검색)이 첫 화면 밖이거나 하단 고정 버튼에 가려 시작 방법을 찾지 못한다.
for (const [width, height] of [[390, 699], [360, 640]]) test(`a first visit sees the search above the fixed button at ${width}x${height}`, async ({ page }) => {
  await page.setViewportSize({ width, height });
  const search = await open(page);
  await expect(search).toBeVisible();
  expect(await aboveBar(page, search)).toBe(true);
  // 불러올 보유가 없는 방문자에게 빈 '불러오기'를 보여 검색창을 밀지 않는다.
  await expect(page.getByRole('button', { name: '저장된 보유 종목 불러오기' })).toHaveCount(0);
  await expect(page.locator('#step-guidance')).toBeHidden();
});

test('a visitor with saved holdings still gets the import shortcut', async ({ page }) => {
  await open(page, { assets: [{ id: 'a', instrument_id: 'US:VOO', name: 'VOO', account: '일반 계좌', category: '주식', amount: 1000000 }] });
  await expect(page.getByRole('button', { name: '저장된 보유 종목 불러오기' })).toBeVisible();
});

for (const [width, height] of [[390, 844], [360, 640], [320, 568]]) test(`keyboard entry never hides the next field behind the fixed button at ${width}x${height}`, async ({ page }) => {
  await page.setViewportSize({ width, height });
  const search = await open(page);
  for (const query of ['VOO', 'QQQM', '069500', '360750']) {
    await search.fill(query);
    await page.keyboard.press('Enter');
    expect(await aboveBar(page, page.locator(':focus'))).toBe(true);
    await page.keyboard.type('1000000');
    await page.keyboard.press('Enter');
    await expect(search).toBeFocused();
    expect(await aboveBar(page, search)).toBe(true);
  }
  // 모바일 미리보기는 3줄까지만 — 나머지는 아래 계좌 카드에 있다.
  await expect(page.locator('.portfolio-preview li.more')).toHaveText('외 1개');
  // 작은 도넛 옆 범례에서도 종목명이 한 글자씩 꺾이지 않을 폭을 남긴다(320px 회귀).
  const nameWidths = await page.locator('.portfolio-preview li:not(.more) > span').evaluateAll(spans => spans.filter(s => (s as HTMLElement).offsetParent).map(s => s.getBoundingClientRect().width));
  expect(Math.min(...nameWidths)).toBeGreaterThan(72);
});
