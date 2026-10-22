import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const catalog = JSON.parse(readFileSync(new URL('../../portfolio-worker/src/edge_portfolio/data/etf_catalog.json', import.meta.url), 'utf8'));

// 운영과 같은 화면(미리보기 막대 없음)에서 API 만 흉내 낸다.
async function open(page: Page, saved: unknown = null, latest: unknown = null) {
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/workbook')) return route.fulfill({ json: { version: saved ? 1 : 0, workbook: saved } });
    if (path.endsWith('/analyses/latest')) return route.fulfill({ json: latest });
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

// 선택 항목(요청사항)은 모바일 첫 화면에서 접어 1단계를 훑는 길이를 줄이되, PC 와 이미 적은 요청은 펼친다.
test('the optional request box is folded on phones and open on desktop', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  const request = page.getByLabel('요청사항', { exact: true });
  await expect(request).toBeHidden();
  await page.getByText('요청사항 적기 · 선택', { exact: true }).click();
  await expect(request).toBeVisible();
  // 펼치면 접기 줄이 사라지므로, 키보드 사용자가 길을 잃지 않게 입력칸으로 포커스를 옮긴다.
  await expect(request).toBeFocused();
  // 다음 단계에 갔다 와도 방문자가 연 상자는 열린 채다(적은 요청이 숨지 않게).
  await request.fill('배당 위주로 바꾸고 싶어요');
  await addHolding(page);
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '앞 질문으로' }).click();
  await expect(page.getByLabel('요청사항', { exact: true })).toBeVisible();
  // 새로 열어도 이미 적은 요청이 있으면 모바일에서도 펼쳐 보인다.
  await page.reload();
  await expect(page.getByLabel('요청사항', { exact: true })).toHaveValue('배당 위주로 바꾸고 싶어요');
  await expect(page.getByLabel('요청사항', { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.evaluate(() => sessionStorage.clear());
  await page.reload();
  await expect(page.getByLabel('요청사항', { exact: true })).toBeVisible();
});

async function addHolding(page: Page) {
  await page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' }).fill('VOO');
  await page.keyboard.press('Enter');
  await page.keyboard.type('1000000');
}

// 이탈 분석: 해 볼 가치를 확인하기 전에 떠난다 — 입력 전에 받을 결과를 보여주되, 닫으면 하던 자리로 돌아온다.
test('the example result opens as a picture and returns to where the visitor was', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  const search = await open(page);
  expect(await aboveBar(page, search)).toBe(true); // 예시 링크가 첫 행동을 밀어내지 않는다
  const trigger = page.getByRole('button', { name: '예시 결과 보기' });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '이런 결과를 받아요' });
  await expect(dialog.getByRole('img', { name: /예시 결과/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await dialog.getByRole('button', { name: '예시 결과 닫기' }).click();
  await expect(dialog).toBeHidden();
});

// 처음 온 방문자는 저장한 것이 없어 대시보드가 비어 있다 — 첫 행동 대신 빈 화면으로 빠지기 전에 한 번 묻는다.
test('a first-time visitor is asked before going to an empty dashboard', async ({ page }) => {
  await open(page);
  await page.getByRole('link', { name: '내 포트폴리오 ↗' }).click();
  const ask = page.getByRole('dialog', { name: /아직 처음이라 저장된 포트폴리오가 없어요/ });
  await expect(ask).toBeVisible();
  await ask.getByRole('button', { name: '취소' }).click();
  await expect(page).not.toHaveURL(/view=dashboard/);
  await page.getByRole('link', { name: '내 포트폴리오 ↗' }).click();
  await page.getByRole('button', { name: '대시보드로 가기' }).click();
  await expect(page).toHaveURL(/view=dashboard/);
});

test('a dashboard check that fails never traps the visitor on the input screen', async ({ page }) => {
  await open(page);
  await page.route('**/api/portfolio/workbook', route => route.fulfill({ status: 503, json: {} }));
  await page.getByRole('link', { name: '내 포트폴리오 ↗' }).click();
  await expect(page).toHaveURL(/view=dashboard/);
});

test('a visitor with an earlier analysis but no saved holdings goes straight to the dashboard', async ({ page }) => {
  // 대시보드는 최근 분석도 보여준다 — 분석을 해 본 사람에게 '처음'이라고 묻지 않는다.
  await open(page, null, { id: 'earlier', state: 'succeeded' });
  await page.getByRole('link', { name: '내 포트폴리오 ↗' }).click();
  await expect(page).toHaveURL(/view=dashboard/);
});

test('a visitor with a saved portfolio goes straight to the dashboard', async ({ page }) => {
  await open(page, { assets: [{ id: 'a', instrument_id: 'US:VOO', name: 'VOO', account: '일반 계좌', category: '주식', amount: 1000000 }] });
  await page.getByRole('link', { name: '내 포트폴리오 ↗' }).click();
  await expect(page).toHaveURL(/view=dashboard/);
});
