import { fillForm } from '../scripts/fill-form.mjs';
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { addCash, addHolding, startInput } from './holdings';
const catalog = JSON.parse(readFileSync(new URL('../../portfolio-worker/src/edge_portfolio/data/etf_catalog.json', import.meta.url), 'utf8'));

// 서버 입력 계약(StrictModel, extra=forbid)은 모르는 필드를 422 로 거부한다. 화면은 서버(contracts.py AnalysisInput)가 받는 필드만 보낸다.
// 8차 필드(#1542): 매달 넣을 돈은 원 단위 monthly_krw, '원하는 포트폴리오' 글은 goal_text(참고), 지킬 조건은 칩 constraints 만.
const inputKeys = ['draft_id', 'holdings', 'question', 'horizon', 'input_mode', 'planning', 'preferences', 'monthly_krw', 'goal_text', 'constraints'];
const preferenceKeys = ['accounts', 'contribution', 'dividend_goal', 'interests', 'note', 'tax_profile'];
test('the three input steps reach analysis using only fields the server accepts', async ({ page }) => {
  let submitted: any;
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/analyses') && route.request().method() === 'POST') submitted = route.request().postDataJSON();
    if (path.includes('/analyses')) return route.fulfill({ json: { id: 'test-job', state: 'running', stage: 'question' } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await fillForm(page);
  await expect(page.locator('.progress')).toBeVisible();
  expect(Object.keys(submitted).every(k => inputKeys.includes(k))).toBe(true);
  expect(Object.keys(submitted.preferences).every(k => preferenceKeys.includes(k))).toBe(true);
  expect(submitted).toMatchObject({ input_mode: 'amount', horizon: '10년 이상', planning: false, monthly_krw: 1000000, goal_text: '', constraints: [],
    holdings: [{ instrument_id: 'KR:069500', amount_krw: '7000000' }, { instrument_id: 'KR:360750', amount_krw: '3000000' }],
    preferences: { accounts: ['일반 계좌'], contribution: '매달 꾸준히 100만 원', interests: [], note: '', tax_profile: null } });
});

// 지킬 조건은 고른 칩만 보낸다(글은 참고). '종목 n개 이하'는 서버가 1~20만 받으므로 그 밖은 화면이 먼저 막는다.
test('chosen chips are the only constraints and the position limit is validated before sending', async ({ page }) => {
  let submitted: any;
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/analyses') && route.request().method() === 'POST') submitted = route.request().postDataJSON();
    if (path.includes('/analyses')) return route.fulfill({ json: { id: 'chip-job', state: 'queued' } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await addCash(page);
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '안 넣어요', exact: true }).click();
  await page.getByRole('button', { name: /^3년 안/ }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByLabel('원하는 포트폴리오').fill('레버리지는 빼고 S&P500 위주로');
  const chips = page.getByRole('group', { name: '꼭 지켜야 할 조건' });
  await chips.getByRole('button', { name: '레버리지 빼기' }).click();
  await chips.getByRole('button', { name: '국내 상장 ETF만' }).click();
  await expect(page.getByText('미국 ETF도 국내에 상장된 것이면 돼요')).toBeVisible();
  await chips.getByLabel('최대 종목 수').fill('25');
  await expect(page.locator('#step-guidance')).toHaveText('종목 수는 1~20개로 적어 주세요');
  await expect(page.getByRole('button', { name: '분석 시작하기' })).toBeDisabled();
  // 맞지 않는 종목 수가 있어도 앞 단계로 갔다 오는 초안 저장은 막히지 않는다(서버 초안 계약도 1~20만 받으므로 빼고 저장).
  await page.getByRole('button', { name: '앞 단계로' }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByRole('heading', { name: '거의 다 왔어요' })).toBeVisible();
  await chips.getByLabel('최대 종목 수').fill('5');
  // 고친 숫자는 초안에 남는다.
  await page.reload();
  await expect(chips.getByLabel('최대 종목 수')).toHaveValue('5');
  await page.getByRole('button', { name: '분석 시작하기' }).click();
  await expect.poll(() => submitted?.constraints).toEqual([{ kind: 'no_leverage' }, { kind: 'kr_listed_etf_only' }, { kind: 'max_positions', value: 5 }]);
  expect(submitted.goal_text).toBe('레버리지는 빼고 S&P500 위주로');
  expect(submitted.monthly_krw).toBeNull();
  expect(submitted.preferences.note).toBe('');
});

test('timeout explains interruption and preserved input without blaming customer data', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'timed-out'));
  await page.route('**/api/portfolio/**', route => route.fulfill({ json: { id: 'timed-out', state: 'failed', failure: 'TIMED_OUT' } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.r8-failed')).toContainText('입력한 내용은 그대로 있어요');
  await expect(page.locator('.r8-failed')).toContainText('오래 걸려 멈췄어요');
  await expect(page.locator('.r8-failed')).not.toContainText('5분 안에');
});

test('external search throttling waits before retrying and is distinguished from the visitors trial quota', async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'search-limited'));
  await page.route('**/api/portfolio/analyses/search-limited', route => route.fulfill({ json: { id: 'search-limited', state: 'failed', failure: 'SEARCH_RATE_LIMIT', input: { holdings: [], horizon: '3년 안', question: '' } } }));
  await page.goto('/portfolio/');
  await expect(page.getByRole('heading', { name: /잠시 응답하지 않아요/ })).toBeVisible();
  await expect(page.locator('.fl-state')).not.toContainText('오늘 체험 횟수');
  // 막힌 자료 서버에 바로 다시 보내면 같은 이유로 멈춘다 — 1분 뒤에야 다시 시도를 누를 수 있다.
  await expect(page.getByRole('button', { name: /초 뒤에 다시 시도/ })).toBeDisabled();
  await page.clock.runFor(61000);
  await expect(page.getByRole('button', { name: '같은 입력으로 다시 분석' })).toBeEnabled();
});

test('an analysis this browser cannot open offers a new analysis and clears the stale pointer', async ({ page }) => {
  await page.route('**/api/portfolio/workbook/catalog', route => route.fulfill({ json: catalog }));
  await page.route('**/api/portfolio/analyses/missing-job', route => route.fulfill({ status: 404, json: {} }));
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'missing-job'));
  await page.goto('/portfolio/');
  await expect(page.getByRole('heading', { name: /지난 분석을 찾을 수 없어요/ })).toBeVisible();
  // 다른 기기에서는 열리지 않는다(분석은 방문자 쿠키에 묶여 있다) — 그렇게 약속하지 않는다.
  await expect(page.locator('.fl-state')).not.toContainText('다른 기기');
  await page.getByRole('button', { name: '새로 분석하기', exact: true }).click();
  await expect(page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('edge-portfolio-job'))).toBeNull();
});

test('real stages, result restoration and chat gate', async ({ page }) => {
  let ready = false;
  const section = { paragraphs: ['검증용 포트폴리오 설명이에요.', '검증용 판단 조건이에요.'], evidence: [], limitations: [] };
  await page.route('**/api/portfolio/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/chat')) return route.fulfill({ status: 409, json: { detail: 'CHAT_LIMIT' } });
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: Array.from({ length: 5 }, (_, i) => ({ id: `used-${i}`, state: 'succeeded', input: { question: '이미 물어본 질문' }, result: { answer: section } })) } });
    if (path.endsWith('/newsletter')) {
      expect(route.request().postDataJSON()).toEqual({ email: 'test@example.com', consent: true });
      return route.fulfill({ status: 201, json: { status: 'pending', delivery_ready: true } });
    }
    expect(path).not.toContain('/report-request');
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    const result = { sections: { question: section, portfolio: section, outlook: section, opinion: section }, sources: [], as_of: '2026-10-12T00:00:00Z', metrics: { total_krw: '1000000', holdings: [{ name: 'VOO', account: '일반 계좌', weight_pct: 100 }], unavailable_reason: '시계열 미연결' } };
    return route.fulfill({ json: { id: 'test-job', state: ready ? 'succeeded' : 'running', stage: 'portfolio', started_at: new Date().toISOString(), result: ready ? result : null,
      input: { holdings: [{ name: 'KODEX 200', kind: 'etf', account: '일반 계좌', amount_krw: '7000000' }, { name: 'TIGER 미국S&P500', kind: 'etf', account: '일반 계좌', amount_krw: '3000000' }], horizon: '10년 이상', question: '3년 뒤 결혼 자금으로 써도 괜찮을까요?' } } });
  });
  await page.goto('/portfolio/');
  await fillForm(page, { question: '3년 뒤 결혼 자금으로 써도 괜찮을까요?' });
  await expect(page.locator('.progress')).toContainText('보통 5분');
  await expect(page.locator('.progress-steps')).toHaveCount(0);
  await expect(page.getByRole('list', { name: '분석 역할과 검토 흐름' })).toBeVisible();
  await expect(page.locator('.fl-agent')).toHaveCount(5);
  // 서버의 실제 단계(portfolio)가 종목·산업 두 역할을 '검토 중'으로 보인다 — 꾸민 진행이 아니다.
  await expect(page.locator('.fl-agent[data-s="run"]')).toHaveCount(2);
  await expect(page.getByRole('heading', { name: /종목 2개를/ })).toBeVisible();
  // 물어본 것을 기다리는 동안에도 보여 준다(결과 맨 위에서 이 질문에 먼저 답한다).
  await expect(page.locator('.fl-asked')).toContainText('3년 뒤 결혼 자금으로 써도 괜찮을까요?');
  await expect(page.locator('.progress')).not.toContainText('겹');
  await page.screenshot({ path: `test-results/progress-${test.info().project.name}.png`, fullPage: true });
  ready = true;
  await page.reload();
  // 추천이 없는 옛 보고서도 같은 칸 차례로 열리고, 지금 가진 구성을 최종 칸에 그대로 보여 준다.
  await expect(page.locator('.r8-headline')).toHaveText('이번에는 추천을 만들지 못했어요');
  await expect(page.locator('[data-analytics-section=final]')).toContainText('VOO');
  await expect(page.locator('[data-analytics-section=analysis] .r8-item')).toHaveCount(3);
  // 이 분석의 질문 5회를 다 쓴 고객은 입력창이 잠기고 이유와 다음 길(새 분석)을 본다.
  await expect(page.getByRole('textbox', { name: '직접 물어보기', exact: true })).toBeDisabled();
  await expect(page.locator('[data-analytics-section=ask]')).toContainText('이 분석의 질문 5번을 모두 썼어요. 새 분석에서 다시 물을 수 있어요.');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/report-${test.info().project.name}.png`, fullPage: true });
});

test('empty start, keyboard holding entry, live basket and the account rules of the server', async ({ page }) => {
  await page.route('**/api/portfolio/session', route => route.fulfill({ json: {} }));
  await page.route('**/api/portfolio/drafts/**', route => route.fulfill({ json: { status: 'saved' } }));
  await page.route('**/api/portfolio/workbook/catalog', route => route.fulfill({ json: catalog }));
  await page.goto('/portfolio/');
  await startInput(page);
  // No invented cash: the visitor starts from their own holdings.
  await expect(page.locator('.fl-hold')).toHaveCount(0);
  await expect(page.locator('.fl-basket')).toHaveCount(0);
  const search = page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' });
  // Pick → account → amount → Enter → next search, without reaching for the pointer.
  await search.fill('VOO');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: '어느 계좌에 있나요?' })).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('textbox', { name: 'Vanguard S&P 500 ETF 금액' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(search).toBeFocused();
  await search.fill('현금');
  await page.keyboard.press('Enter');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('textbox', { name: '현금 금액' })).toBeFocused();
  await page.keyboard.type('1000000');
  await expect(page.getByRole('textbox', { name: '현금 금액' })).toHaveValue('1,000,000');
  await expect(page.locator('.fl-basket__total')).toHaveText('100만 원');
  await search.fill('zzzz-없는종목');
  await expect(page.getByText('맞는 종목이 없어요')).toBeVisible();
  await search.fill('');
  await page.getByRole('button', { name: 'Vanguard S&P 500 ETF 빼기' }).click();
  await page.getByRole('button', { name: '현금 빼기' }).click();
  await expect(page.locator('.fl-basket')).toHaveCount(0);
  // 서버 규칙과 같다: 미국 상장 종목은 일반 계좌에만, 개별주는 연금저축·IRP 에 못 담는다(고를 수 없게 막고 까닭을 적는다).
  await search.fill('VOO');
  await page.getByRole('button', { name: 'Vanguard S&P 500 ETF 담기' }).click();
  const sheet = page.getByRole('dialog', { name: '어느 계좌에 있나요?' });
  for (const account of ['ISA', '연금저축', 'IRP']) await expect(sheet.getByRole('button', { name: new RegExp('^' + account) })).toBeDisabled();
  await expect(sheet).toContainText('미국 상장은 일반 계좌만');
  await page.keyboard.press('Escape');
  await addHolding(page, '069500', 'KODEX 200', '100', 'ISA');
  await expect(page.locator('.fl-group').filter({ hasText: 'ISA' })).toContainText('KODEX 200');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByRole('heading', { name: /앞으로의 계획을/ })).toBeVisible();
  // 종목이 담긴 계좌는 골라 둔 채로 잠근다(가진 돈이 있는 곳이다).
  await expect(page.getByRole('button', { name: /^ISA/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: /^ISA/ })).toBeDisabled();
  await page.screenshot({ path: `test-results/plan-${test.info().project.name}.png`, fullPage: true });
});

test('plan answers persist into the draft as the existing funding text and tax profile', async ({ page }) => {
  const saved: any[] = [];
  await page.route('**/api/portfolio/**', async route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().url().includes('/drafts/')) saved.push(route.request().postDataJSON());
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await addCash(page);
  await page.getByRole('button', { name: '다음', exact: true }).click();
  const next = page.getByRole('button', { name: '다음', exact: true });
  await expect(next).toBeDisabled();
  await expect(page.locator('#step-guidance')).toHaveText('매달 넣을 돈을 골라 주세요');
  await page.getByRole('button', { name: '30만', exact: true }).click();
  await page.getByRole('button', { name: /^3년 안/ }).click();
  await page.getByRole('button', { name: /^ISA/ }).click();
  await page.getByRole('button', { name: /^IRP/ }).click();
  // 새로 고른 ISA·IRP 는 가입할 수 있는지 먼저 묻는다(답하지 않으면 넘어가지 않는다).
  await expect(page.locator('#step-guidance')).toHaveText('ISA 확인 질문에 답해 주세요');
  await page.getByRole('group', { name: '국내에 살고 있나요?' }).getByRole('button', { name: '예' }).click();
  await page.getByRole('group', { name: /금융소득종합과세/ }).getByRole('button', { name: '아니요' }).click();
  await expect(page.locator('#step-guidance')).toHaveText('IRP 가입 조건을 골라 주세요');
  await page.getByRole('group', { name: 'IRP 가입 조건' }).getByRole('button', { name: '모르겠어요' }).click();
  await expect(page.locator('.fl-summary')).toContainText('매달 30만 원');
  await next.click();
  await expect(page.getByRole('heading', { name: '거의 다 왔어요' })).toBeVisible();
  const draft = saved.at(-1);
  expect(draft.preferences.contribution).toBe('매달 꾸준히 30만 원');
  expect(draft.horizon).toBe('3년 안');
  expect(draft.preferences.accounts).toEqual(['일반 계좌', 'ISA', 'IRP']);
  // 묻지 않은 나이·소득은 '확인 필요'로 보낸다 — 서버 계약은 빈 값을 받지 않는다.
  expect(draft.preferences.tax_profile).toMatchObject({ residence: '대한민국 거주자', financial_income_tax: '아니요', irp_qualification: '확인 필요', age: '확인 필요', income: '확인 필요', recommended_accounts: ['ISA', 'IRP'] });
  await page.getByRole('button', { name: '앞 단계로' }).click();
  await expect(page.getByRole('button', { name: '30만', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: /^IRP/ })).toHaveAttribute('aria-pressed', 'true');
});
