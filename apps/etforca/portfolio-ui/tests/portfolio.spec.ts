import { fillForm } from '../scripts/fill-form.mjs';
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
const catalog = JSON.parse(readFileSync(new URL('../../portfolio-worker/src/edge_portfolio/data/etf_catalog.json', import.meta.url), 'utf8'));

test('an unselected investment objective reaches analysis without inventing a preference', async ({ page }) => {
  let submitted: { preferences: { interests: string[] } } | undefined;
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/analyses') && route.request().method() === 'POST') submitted = route.request().postDataJSON();
    if (path.includes('/analyses')) return route.fulfill({ json: { id: 'test-job', state: 'running', stage: 'question' } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await fillForm(page, { interests: [] });
  await expect(page.locator('.progress')).toBeVisible();
  expect(submitted?.preferences.interests).toEqual([]);
});

test('timeout explains interruption and preserved input without blaming customer data', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'timed-out'));
  await page.route('**/api/portfolio/**', route => route.fulfill({ json: { id: 'timed-out', state: 'failed', failure: 'TIMED_OUT' } }));
  await page.goto('/portfolio/');
  await expect(page.locator('.ans')).toContainText('입력한 내용은 그대로 보관');
  await expect(page.locator('.ans')).not.toContainText('5분 안에');
});

test('external search throttling is distinguished from the visitors trial quota', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'search-limited'));
  await page.route('**/api/portfolio/analyses/search-limited', route => route.fulfill({ json: { id: 'search-limited', state: 'failed', failure: 'SEARCH_RATE_LIMIT' } }));
  await page.goto('/portfolio/');
  await expect(page.getByText(/자료 조회가 잠시 지연/)).toBeVisible();
  await expect(page.locator('.ans')).not.toContainText('오늘 체험 횟수');
});

test('an unavailable saved analysis can return to input without clearing browser storage manually', async ({ page }) => {
  await page.route('**/api/portfolio/catalog', route => route.fulfill({ json: catalog }));
  await page.route('**/api/portfolio/analyses/missing-job', route => route.fulfill({ status: 404, json: {} }));
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'missing-job'));
  await page.goto('/portfolio/');
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByRole('button', { name: '입력 화면으로', exact: true }).click();
  await expect(page.getByRole('heading', { name: '내 포트폴리오를 더 좋게 바꿔볼까요?' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('edge-portfolio-job'))).toBeNull();
});

test('default account, real stages, result restoration, chat gate and email receipt', async ({ page }) => {
  let ready = false;
  const section = { paragraphs: ['검증용 포트폴리오 설명이에요.', '검증용 판단 조건이에요.'], evidence: [], limitations: [] };
  await page.route('**/api/portfolio/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (path.endsWith('/chat')) return route.fulfill({ status: 409, json: { detail: 'CHAT_LIMIT' } });
    if (path.endsWith('/newsletter')) {
      expect(route.request().postDataJSON()).toEqual({ email: 'test@example.com', consent: true });
      return route.fulfill({ status: 201, json: { status: 'pending', delivery_ready: true } });
    }
    expect(path).not.toContain('/report-request');
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    const result = { sections: { question: section, portfolio: section, outlook: section, opinion: section }, sources: [], as_of: '2026-10-12T00:00:00Z', metrics: { total_krw: '1000000', holdings: [{ name: 'VOO', account: '일반 계좌', weight_pct: 100 }], unavailable_reason: '시계열 미연결' } };
    return route.fulfill({ json: { id: 'test-job', state: ready ? 'succeeded' : 'running', stage: 'portfolio', started_at: new Date().toISOString(), result: ready ? result : null } });
  });
  await page.goto('/portfolio/');
  await expect(page.locator('.acct__name').first()).toHaveText('\uC77C\uBC18 \uACC4\uC88C');
  await fillForm(page);
  await expect(page.locator('.progress')).toContainText('보통 5분');
  await expect(page.locator('.progress')).toContainText('최대 10분');
  await expect(page.locator('.progress-steps li')).toHaveCount(4);
  await expect(page.locator('.progress-steps [aria-current="step"]')).toHaveText('보유 구성 분석진행 중');
  await expect(page.locator('.research-motion svg')).toBeVisible();
  await page.screenshot({ path: `test-results/progress-${test.info().project.name}.png`, fullPage: true });
  ready = true;
  await page.reload();
  await expect(page.getByRole('heading', { name: '핵심 평가 정리' })).toBeVisible();
  await expect(page.locator('.evaluation section')).toHaveCount(4);
  await expect(page.locator('.fee')).toContainText('100만 원');
  await expect(page.getByRole('heading', { name: '과거 성과' })).toBeVisible();
  await page.locator('.evaluation').scrollIntoViewIfNeeded();
  await page.getByRole('button', { name: '평가 팝업 닫기' }).click();
  await page.getByLabel('추가 질문', { exact: true }).fill('다시 물어볼게요');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect(page.getByRole('dialog').filter({ hasText: '아직 준비 중' })).toBeVisible();
  await page.getByRole('button', { name: '포트폴리오로 돌아가기' }).click();
  await expect(page.getByRole('textbox', { name: '추가 질문', exact: true })).toBeDisabled();
  await page.locator('.premium').click();
  await page.getByRole('dialog', { name: '내 ETF 핵심 소식 이메일로 받기' }).getByLabel('뉴스레터 이메일').fill('test@example.com');
  await page.getByRole('button', { name: '매일 핵심 소식 받기' }).click();
  await expect(page.getByRole('dialog', { name: '내 ETF 핵심 소식 이메일로 받기' })).toContainText('확인 메일의 링크');
  await expect(page.getByRole('dialog', { name: '내 ETF 핵심 소식 이메일로 받기' })).not.toContainText('1일 이내');
  await page.getByRole('button', { name: '닫기', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/report-${test.info().project.name}.png`, fullPage: true });
});

test('cash default, live donut, ETF-only search and direct recommendation flow', async ({ page }) => {
  await page.route('**/api/portfolio/session', route => route.fulfill({ json: {} }));
  await page.route('**/api/portfolio/drafts/**', route => route.fulfill({ json: { status: 'saved' } }));
  await page.route('**/api/portfolio/catalog', route => route.fulfill({ json: catalog }));
  await page.goto('/portfolio/');
  await expect(page.getByRole('spinbutton', { name: '현금 금액' })).toHaveValue('100');
  await expect(page.locator('.portfolio-preview')).toContainText('100만');
  await expect(page.getByRole('button', { name: '최적의 ISA, IRP 등 세금 혜택과 투자전략까지 정리해드릴까요?' })).toBeVisible();
  await page.getByRole('button', { name: '금액', exact: true }).click();
  await expect(page.getByRole('spinbutton', { name: '현금 금액' })).toHaveValue('100');
  const firstSearch = page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' });
  await firstSearch.fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard S&P 500 ETF' }).click();
  await expect(page.locator('.portfolio-preview li').filter({ hasText: '현금' })).toContainText('100만 원');
  await expect(page.locator('.portfolio-preview li').filter({ hasText: '현금' })).not.toContainText('미입력');
  await page.getByRole('button', { name: 'Vanguard S&P 500 ETF 빼기' }).click();
  await expect(page.getByRole('button', { name: '계좌별로 추천받기' })).toHaveCount(0);
  await page.getByRole('button', { name: '현금 빼기' }).click();
  await expect(page.locator('.portfolio-preview')).toHaveCount(0);
  await page.getByRole('button', { name: '+ISA', exact: true }).click();
  const find = page.getByRole('combobox', { name: 'ISA에 담은 ETF 찾기' });
  await find.fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard S&P 500 ETF' }).click();
  await expect(page.locator('.acct').filter({ hasText: 'ISA' })).toContainText('Vanguard S&P 500 ETF');
  await expect(page.locator('.portfolio-preview')).toBeVisible();
  await expect(find).toHaveCSS('background-image', 'none');
  await find.fill('현금');
  await page.getByRole('option', { name: '현금 원화 현금' }).click();
  await expect(page.getByRole('spinbutton', { name: '현금 금액' })).toBeVisible();
  await page.getByRole('spinbutton', { name: '현금 금액' }).fill('100');
  await page.getByRole('spinbutton', { name: 'Vanguard S&P 500 ETF 금액' }).fill('100');
  await page.getByRole('button', { name: /종목부터 추천받기/ }).click();
  await expect(page.getByRole('heading', { name: '어떻게 투자할 계획인가요?' })).toBeVisible();
  await expect(page.getByRole('button', { name: '이미 다 매수한 상태에요' })).toBeVisible();
  await page.getByRole('button', { name: '매달 꾸준히', exact: true }).click();
  await page.getByLabel('추가 투자금액').fill('200');
  await page.getByRole('button', { name: '목돈 한 번에', exact: true }).click();
  await expect(page.getByLabel('추가 투자금액')).toHaveCount(0);
  const saved = page.waitForRequest(r => r.url().includes('/drafts/') && r.method() === 'POST');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  expect((await saved).postDataJSON().preferences.contribution).toBe('목돈 한 번에');
  await expect(page.getByRole('heading', { name: '언제쯤 쓸 돈인가요?' })).toBeVisible();
  await page.screenshot({ path: `test-results/recommend-${test.info().project.name}.png`, fullPage: true });
});

test('tax details and custom interests persist without a dividend step', async ({ page }) => {
  const saved: any[] = [];
  await page.route('**/api/portfolio/**', async route => {
    if (route.request().url().endsWith('/catalog')) return route.fulfill({ json: catalog });
    if (route.request().url().includes('/drafts/')) saved.push(route.request().postDataJSON());
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await expect(page.getByRole('heading', { name: '내 포트폴리오를 더 좋게 바꿔볼까요?' })).toBeVisible();
  await page.getByRole('button', { name: '+ISA', exact: true }).click();
  await page.getByRole('button', { name: '최적의 ISA, IRP 등 세금 혜택과 투자전략까지 정리해드릴까요?' }).click();
  await expect(page.locator('input[type=radio]:checked')).toHaveCount(0);
  await page.getByRole('button', { name: '선택한 내용 적용하기' }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('연간 소득 구간');
  await page.getByRole('button', { name: '계좌 정보 닫기' }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByRole('heading', { name: '어떻게 투자할 계획인가요?' })).toBeVisible();
  await page.getByRole('button', { name: '앞 질문으로' }).click();
  await page.locator('.tax-trigger').click();
  await page.getByRole('group', { name: '연간 소득 구간', exact: true }).getByRole('radio', { name: '확인 필요', exact: true }).check();
  await page.getByRole('radio', { name: '대한민국 거주자', exact: true }).check();
  await page.getByRole('radio', { name: '19~54세', exact: true }).check();
  await page.getByRole('radio', { name: '아니요', exact: true }).check();
  await page.getByRole('radio', { name: '중개형 · 일반형', exact: true }).check();
  await page.getByRole('checkbox', { name: 'IRP', exact: true }).check();
  await page.getByRole('checkbox', { name: 'ISA', exact: true }).check();
  await page.getByRole('checkbox', { name: '연금저축', exact: true }).check();
  await page.getByRole('checkbox', { name: 'ISA', exact: true }).uncheck();
  await page.screenshot({ path: `test-results/tax-${test.info().project.name}.png` });
  await page.getByRole('button', { name: '선택한 내용 적용하기' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(saved.at(-1).preferences.tax_profile.accounts[1].subtype).toBe('중개형 · 일반형');
  expect(saved.at(-1).preferences.tax_profile.recommended_accounts).toEqual(['IRP', '연금저축']);
  expect(saved.at(-1).preferences.accounts).toEqual(['일반 계좌', 'ISA']);
  await page.getByRole('button', { name: '최적의 ISA, IRP 등 세금 혜택과 투자전략까지 정리해드릴까요?' }).click();
  await expect(page.getByRole('checkbox', { name: 'IRP', exact: true })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'ISA', exact: true })).not.toBeChecked();
  await page.getByRole('button', { name: '계좌 정보 닫기' }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '이미 다 매수한 상태에요' }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '3년 안', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByRole('heading', { name: '더 담고 싶은 쪽이 있나요?' })).toBeVisible();
  await expect(page.locator('.bar .btn')).toBeEnabled();
  await page.getByLabel('관심 키워드 직접 입력').fill('환율 부담');
  await page.getByRole('button', { name: '추가', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByRole('heading', { name: '추가적으로 고려할 것들을 알려주세요' })).toBeVisible();
  expect(saved.at(-1).preferences.interests).toContain('환율 부담');
  expect(saved.at(-1).preferences.contribution).toBe('이미 다 매수한 상태에요');
});
