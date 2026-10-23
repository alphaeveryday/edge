import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { startInput } from './holdings';

const catalog = JSON.parse(readFileSync(new URL('../../portfolio-worker/src/edge_portfolio/data/etf_catalog.json', import.meta.url), 'utf8'));

// 운영과 같은 화면(미리보기 막대 없음)에서 API 만 흉내 낸다.
async function open(page: Page, saved: unknown = null, latest: unknown = null) {
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/workbook')) return route.fulfill({ json: { version: saved ? 1 : 0, workbook: saved } });
    if (path.endsWith('/analyses/latest')) return route.fulfill({ json: latest });
    if (latest && path.endsWith('/analyses/' + (latest as { id: string }).id)) return route.fulfill({ json: latest });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
}
const search = (page: Page) => page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' });
async function aboveBar(page: Page, element: ReturnType<Page['locator']>) {
  const box = (await element.boundingBox())!, bar = (await page.locator('.fl-cta').boundingBox())!;
  return box.y >= 0 && box.y + box.height <= bar.y;
}

// 이탈 분석: 해 볼 가치를 확인하기 전에 떠난다 — 첫 화면은 받을 결과를 결과와 같은 꼴(판정, 금액 → 금액)로 먼저 보이고,
// 첫 행동(내 포트폴리오 넣기)은 작은 휴대폰에서도 화면 안에 있다.
for (const [width, height] of [[390, 699], [360, 640]]) test(`a first visit sees the example verdict and the first action at ${width}x${height}`, async ({ page }) => {
  await page.setViewportSize({ width, height });
  await open(page);
  await expect(page.getByRole('heading', { name: /이대로 가도 될까요/ })).toBeVisible();
  const example = page.getByRole('region', { name: '이런 결과를 받아요' });
  await expect(example).toContainText('하나만 바꾸면 돼요');
  await expect(example).toContainText('250만 원 → 0원 전부 팔기');
  await expect(page.getByRole('button', { name: '내 포트폴리오 넣기' })).toBeInViewport({ ratio: 1 });
  // 처음 온 방문자에게 지난 분석이나 '바꾸셨나요?'를 묻지 않는다.
  await expect(page.getByText('바꾸셨나요?')).toHaveCount(0);
});

// 입력 중단 분석의 1순위 가설: 첫 행동(종목 검색)이 첫 화면 밖이거나 하단 고정 버튼에 가려 시작 방법을 찾지 못한다.
for (const [width, height] of [[390, 699], [360, 640]]) test(`the holdings step shows the search above the fixed button at ${width}x${height}`, async ({ page }) => {
  await page.setViewportSize({ width, height });
  await open(page);
  await startInput(page);
  await expect(search(page)).toBeVisible();
  expect(await aboveBar(page, search(page))).toBe(true);
  // 불러올 보유가 없는 방문자에게 빈 '불러오기'를 보여 검색창을 밀지 않는다.
  await expect(page.getByRole('button', { name: /저장된 보유 종목 불러오기/ })).toHaveCount(0);
});

test('a visitor with saved holdings still gets the import shortcut', async ({ page }) => {
  await open(page, { assets: [{ id: 'a', instrument_id: 'US:VOO', name: 'VOO', account: '일반 계좌', category: '주식', amount: 1000000 }] });
  await startInput(page);
  await expect(page.getByRole('button', { name: /저장된 보유 종목 불러오기/ })).toBeVisible();
});

for (const [width, height] of [[390, 844], [360, 640], [320, 568]]) test(`keyboard entry never hides the next field behind the fixed button at ${width}x${height}`, async ({ page }) => {
  await page.setViewportSize({ width, height });
  await open(page);
  await startInput(page);
  for (const query of ['VOO', 'QQQM', '069500', '360750']) {
    await search(page).fill(query);
    await page.keyboard.press('Enter');
    await page.keyboard.press('Enter'); // 계좌 시트: 기본 계좌로 담기
    expect(await aboveBar(page, page.locator(':focus'))).toBe(true);
    await page.keyboard.type('1000000');
    await page.keyboard.press('Enter');
    await expect(search(page)).toBeFocused();
    expect(await aboveBar(page, search(page))).toBe(true);
  }
  await expect(page.locator('.fl-basket__total')).toHaveText('400만 원');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('the example result opens as a picture and returns to where the visitor was', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 });
  await open(page);
  const trigger = page.getByRole('button', { name: '예시 보기' });
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '이런 결과를 받아요' });
  await expect(dialog.getByRole('img', { name: /예시 결과/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

// S2: 다시 온 방문자에게는 지난 판정을 먼저 보이고 "바꾸셨나요?"로 다음 길을 고르게 한다.
const latest = { id: 'earlier', state: 'succeeded', created_at: '2026-10-21T02:00:00Z',
  input: { holdings: [{ name: 'ACE 글로벌반도체TOP4 Plus', instrument_id: 'KR:446770', kind: 'etf', account: '일반 계좌', amount_krw: '2500000' }], horizon: '10년 이상', question: '', input_mode: 'amount',
    preferences: { accounts: ['일반 계좌'], contribution: '이미 다 매수한 상태에요', interests: [], note: '', tax_profile: null } },
  result: { sections: {}, sources: [], recommendation: { execution: { status: 'ready', rows: [
    { instrument_id: 'KR:446770', name: 'ACE 글로벌반도체TOP4 Plus', account: '일반 계좌', before_krw: '2500000', target_krw: '0', buy_krw: '0', sell_krw: '2500000', cash_used_krw: '0' },
    { instrument_id: 'KR:360750', name: 'TIGER 미국S&P500', account: '일반 계좌', before_krw: '0', target_krw: '2500000', buy_krw: '2500000', sell_krw: '0', cash_used_krw: '0' }] } } } };
test('a returning visitor sees the last verdict and either edits a prefilled input or reopens the result', async ({ page }) => {
  await open(page, null, latest);
  await expect(page.getByRole('heading', { name: /다시 오셨네요/ })).toBeVisible();
  const card = page.getByRole('region', { name: '지난 분석' });
  await expect(card).toContainText('10월 21일');
  // 판정은 실행표에서 계약과 같은 규칙으로 정한다: 바뀌는 종목이 둘이면 '하나만'이 아니다.
  await expect(card).toContainText('여러 종목을 바꾸는 게 나아요');
  await expect(card).toContainText('0원 → 250만 원 새로 사기');
  await expect(page.getByRole('link', { name: /내 포트폴리오/ })).toHaveAttribute('href', '?view=dashboard');
  await card.getByRole('button', { name: '바꿨어요' }).click();
  await expect(page.getByRole('textbox', { name: 'ACE 글로벌반도체TOP4 Plus 금액' })).toHaveValue('2,500,000');
  await page.getByRole('button', { name: '처음 화면으로' }).click();
  await page.getByRole('button', { name: '아직이요' }).click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('edge-portfolio-job'))).toBe('earlier');
});

// 계산하지 못한 지난 분석(실행표 없음)을 '이대로 가도 돼요'로 읽으면 고객에게 틀린 판정을 준다.
test('a past analysis without a computed execution is not shown as keep', async ({ page }) => {
  // 한도 초과(infeasible)로 실행할 수 없는 표는 줄이 남아 있어도 판정·금액을 보이지 않는다.
  await open(page, null, { ...latest, result: { ...latest.result, recommendation: { execution: { ...latest.result.recommendation.execution, status: 'infeasible' } } } });
  const card = page.getByRole('region', { name: '지난 분석' });
  await expect(card).toContainText('지난 분석을 다시 볼 수 있어요');
  await expect(card).not.toContainText('바꾸는 게 나아요');
  await expect(card).not.toContainText('새로 사기');
});

// 지난 분석으로 시작해 고친 입력은 처음 화면에 다녀와도 지난 분석 입력으로 덮이지 않는다.
test('an edited restart survives a round trip through the return screen', async ({ page }) => {
  await open(page, null, latest);
  await page.getByRole('button', { name: '바뀐 포트폴리오로 다시 분석' }).click();
  await page.getByRole('textbox', { name: 'ACE 글로벌반도체TOP4 Plus 금액' }).fill('3000000');
  await page.getByRole('button', { name: '처음 화면으로' }).click();
  await expect(page.getByText('작성하던 입력이 있어요')).toBeVisible();
  await page.getByRole('button', { name: '바뀐 포트폴리오로 다시 분석' }).click();
  await expect(page.getByRole('textbox', { name: 'ACE 글로벌반도체TOP4 Plus 금액' })).toHaveValue('3,000,000');
});

// 지난 입력으로 시작한 것은 그 한 번뿐이다 — 그 분석이 끝난 뒤 새 분석은 지난 보유를 다시 채우지 않는다.
test('a prefilled restart is used once and a later new analysis starts empty', async ({ page }) => {
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/analyses/latest')) return route.fulfill({ json: latest });
    if (path.endsWith('/analyses') && route.request().method() === 'POST') return route.fulfill({ json: { id: 'done', state: 'queued' } });
    if (path.endsWith('/analyses/done')) return route.fulfill({ json: { id: 'done', state: 'succeeded', result: { sections: { opinion: { paragraphs: ['테스트 결론'], evidence: [], limitations: [] } }, sources: [] } } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await page.getByRole('button', { name: '바뀐 포트폴리오로 다시 분석' }).click();
  await expect(page.getByRole('textbox', { name: 'ACE 글로벌반도체TOP4 Plus 금액' })).toHaveValue('2,500,000');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '분석 시작하기' }).click();
  await page.getByRole('button', { name: '새 포트폴리오 분석하기' }).click();
  await expect(page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' })).toBeVisible();
  await expect(page.locator('.fl-hold')).toHaveCount(0);
});
