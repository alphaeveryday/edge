import { test, expect } from '@playwright/test';

const section = { paragraphs: ['입력 내용을 바탕으로 판단했어요.', '조건을 확인해 주세요.', '기존 보고서는 유지해요.'], evidence: [], limitations: [] };
const report = { sections: { question: section, portfolio: section, outlook: section, opinion: section },
  sources: [], metrics: { total_krw: '1000000', holdings: [], unavailable_reason: '' }, as_of: '2026-10-14T00:00:00Z' };

test('an unavailable saved target renders its reason instead of crashing the revision', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'parent'));
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chat')) return route.fulfill({ status: 202, json: { id: 'child', state: 'queued' } });
    if (path.endsWith('/child')) return route.fulfill({ json: { id: 'child', state: 'succeeded', result: {
      answer: section, revision: { ...report, execution: { status: 'needs_input', reasons: ['SAVED_TARGET_UNAVAILABLE'],
        rows: [], accounts: [], assumptions: [], additional_krw: '0', monthly_krw: null, required_additional_krw: null, months: null },
        completion: { status: 'needs_input', tasks: [], missing_inputs: [], missing_research: [] } },
    } } });
    return route.fulfill({ json: { id: 'parent', state: 'succeeded', result: report } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('추가 질문', { exact: true }).fill('기존 목표를 실행할 수 있나요?');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await page.getByText('보완 분석과 계산표 보기').click();
  await expect(page.locator('.followup-revision .execution-plan')).toContainText('기존 목표의 종목이나 계좌를 현재 조건으로 사용할 수 없어');
});

test('recovery remains available after the question quota and keeps the original report', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'parent'));
  const modes: string[] = [];
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chat')) {
      const body = route.request().postDataJSON(); modes.push(body.mode || 'question');
      return body.mode === 'recovery' ? route.fulfill({ status: 202, json: { id: 'recovered', state: 'queued' } })
        : route.fulfill({ status: 409, json: { detail: 'CHAT_LIMIT' } });
    }
    if (path.endsWith('/recovered')) return route.fulfill({ json: { id: 'recovered', state: 'succeeded', result: {
      answer: section, status: 'complete', revision: { ...report, scenarios: { status: 'available',
        principal_krw: '1000000', monthly_krw: '0', assumptions: ['미래 수익을 보장하지 않아요.'],
        rows: [{ annual_return_pct: '0', years: 5, paid_krw: '1000000', future_krw: '1000000' }] },
        completion: { status: 'complete', tasks: [], missing_inputs: [], missing_research: [] } },
    } } });
    return route.fulfill({ json: { id: 'parent', state: 'succeeded', result: report } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('추가 질문', { exact: true }).fill('5년 금액 계산해 주세요');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await page.getByRole('button', { name: '포트폴리오로 돌아가기' }).click();
  await expect(page.getByLabel('추가 질문', { exact: true })).toBeDisabled();
  await page.getByLabel('질문 종류').selectOption('recovery');
  await expect(page.getByLabel('추가 질문', { exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await page.getByText('보완 분석과 계산표 보기').click();
  await expect(page.locator('.followup-revision .scenario-plan')).toContainText('5년');
  await expect(page.locator('.followup-revision .scenario-plan')).toContainText('1,000,000원');
  expect(modes).toEqual(['question', 'recovery']);
  expect(await page.evaluate(() => localStorage.getItem('edge-portfolio-job'))).toBe('parent');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('an interrupted parent offers a recovery request and a failed recovery can be retried', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'failed-parent'));
  let submitted = 0;
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chat')) {
      expect(route.request().postDataJSON().mode).toBe('recovery'); submitted++;
      return route.fulfill({ status: 202, json: { id: 'failed-child', state: 'queued' } });
    }
    return route.fulfill({ json: { id: path.endsWith('/failed-child') ? 'failed-child' : 'failed-parent', state: 'failed', failure: 'TIMED_OUT' } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('추가 질문', { exact: true }).fill('중단된 질문에 답해 주세요');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect(page.getByText('답변을 만들지 못했어요. 다시 질문할 수 있어요.')).toBeVisible();
  await expect(page.getByLabel('추가 질문', { exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect.poll(() => submitted).toBe(2);
});

test('a second recovery with shorter history resets the previous chart selection', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'parent'));
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  let submitted = 0;
  const revision = (length: number) => ({ ...report, recommendation: { title: '비교 구성',
    evaluation: { scores: null, overall: null, reason: section }, allocations: [], actions: [], total_krw: '1000000',
    historical_performance: { basis: '공통 비교 기간', source_url: 'https://example.com/prices', currency: 'KRW',
      points: Array.from({ length }, (_, i) => ({ date: new Date(Date.UTC(2026, 9, 1 + i)).toISOString().slice(0, 10), recommended: 1000000 + i, current: 1000000 })) } } });
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chat')) return route.fulfill({ status: 202, json: { id: `child-${++submitted}`, state: 'queued' } });
    if (/child-\d$/.test(path)) return route.fulfill({ json: { id: `child-${submitted}`, state: 'succeeded',
      result: { answer: section, revision: revision(submitted === 1 ? 30 : 2) } } });
    return route.fulfill({ json: { id: 'parent', state: 'succeeded', result: report } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('질문 종류').selectOption('recovery');
  await page.getByLabel('추가 질문', { exact: true }).fill('첫 보완 요청');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await page.getByText('보완 분석과 계산표 보기').click();
  await page.getByRole('slider', { name: '과거 성과 날짜 선택' }).fill('29');
  await page.getByLabel('추가 질문', { exact: true }).fill('두 번째 보완 요청');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect(page.locator('.followup-revision .history-date')).toHaveText('2026-10-02');
  expect(errors).toEqual([]);
});
