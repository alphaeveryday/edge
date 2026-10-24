import { test, expect } from '@playwright/test';

const section = { paragraphs: ['입력 내용을 바탕으로 판단했어요.', '조건을 확인해 주세요.', '기존 보고서는 유지해요.'], evidence: [], limitations: [] };
const report = { sections: { question: section, portfolio: section, outlook: section, opinion: section },
  sources: [], metrics: { total_krw: '1000000', holdings: [], unavailable_reason: '' }, as_of: '2026-10-14T00:00:00Z' };

// 누락·오류 보완은 끝내지 못한 분석에서만 연다(8차 결과의 추가 질문은 '더 물어보기' 칸). 다시 보내도 같은 요청으로 묶여야 두 번 차감되지 않는다.
test('an unresolved recovery keeps its idempotency key when sent again', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'parent'));
  const keys: string[] = [];
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: [] } });
    if (path.endsWith('/chat')) {
      keys.push(route.request().headers()['idempotency-key']);
      return keys.length === 1 ? route.abort('failed') : route.fulfill({ status: 202, json: { id: 'child', state: 'queued' } });
    }
    if (path.endsWith('/child')) return route.fulfill({ json: { id: 'child', state: 'succeeded', result: { answer: section } } });
    return route.fulfill({ json: { id: 'parent', state: 'failed', failure: 'TIMED_OUT' } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('추가 질문', { exact: true }).fill('계산을 보완해 주세요');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect(page.getByLabel('추가 질문', { exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect.poll(() => keys.length).toBe(2);
  expect(keys[1]).toBe(keys[0]);
});

for (const [status, reason, explanation] of [
  ['needs_input', 'SAVED_TARGET_UNAVAILABLE', '기존 목표의 종목이나 계좌를 현재 조건으로 사용할 수 없어'],
  ['needs_input', 'CUSTOMER_CONSTRAINT_NOT_SATISFIED', '기존 목표가 현재 고객 필수 조건을 만족하는지 확인해야 해요.'],
  ['infeasible', 'CUSTOMER_CONSTRAINT_NOT_SATISFIED', '기존 목표가 요청하신 조건에 맞지 않아 실행할 수 없어요.'],
]) test(`saved target explains ${status} / ${reason} without displaying trades`, async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'parent'));
  const asked: string[] = [];
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chat')) { asked.push(route.request().postDataJSON().question); return route.fulfill({ status: 202, json: { id: 'child', state: 'queued' } }); }
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: asked.map(question => ({ id: 'child', state: 'succeeded', input: { question }, result: {
      answer: section, revision: { ...report, execution: { status, reasons: [reason],
        rows: [], accounts: [], assumptions: [], additional_krw: '0', monthly_krw: null, required_additional_krw: null, months: null },
        completion: { status: 'needs_input', tasks: [], missing_inputs: [], missing_research: [] } },
    } })) } });
    return route.fulfill({ json: { id: 'parent', state: 'succeeded', result: report } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('직접 물어보기', { exact: true }).fill('기존 목표를 실행할 수 있나요?');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await page.getByText('이 답의 계산표와 근거 보기').click();
  await expect(page.locator('.followup-revision .execution-plan')).toContainText(explanation);
  await expect(page.locator('.followup-revision .execution-plan table')).toHaveCount(0);
  if (status === 'infeasible') await expect(page.locator('.followup-revision .execution-plan')).not.toContainText('확인해야 해요.');
  await expect(page.locator('.followup-revision')).not.toContainText('추천을 보류했어요.');
});

test('a recovery revision keeps the original report and shows very large scenario amounts exactly', async ({ page }) => {
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
        rows: [{ annual_return_pct: '0', years: 5, paid_krw: '1000000', future_krw: '1000000' },
          { annual_return_pct: '100', years: 50, paid_krw: '1000000', future_krw: '9007199254740993' }] },
        completion: { status: 'complete', tasks: [], missing_inputs: [], missing_research: [] } },
    } } });
    return route.fulfill({ json: { id: 'parent', state: 'failed', failure: 'TIMED_OUT' } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('추가 질문', { exact: true }).fill('5년 금액 계산해 주세요');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await page.getByText('보완 분석과 계산표 보기').click();
  await expect(page.locator('.followup-revision .scenario-plan')).toContainText('5년');
  await expect(page.locator('.followup-revision .scenario-plan')).toContainText('1,000,000원');
  await expect(page.locator('.followup-revision .scenario-plan')).toContainText('9,007,199,254,740,993원');
  expect(modes).toEqual(['recovery']);
  expect(await page.evaluate(() => localStorage.getItem('edge-portfolio-job'))).toBe('parent');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('an interrupted parent offers a recovery request and a failed recovery can be retried', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'failed-parent'));
  let submitted = 0;
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: [] } });
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
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: [] } });
    if (path.endsWith('/chat')) return route.fulfill({ status: 202, json: { id: `child-${++submitted}`, state: 'queued' } });
    if (/child-\d$/.test(path)) return route.fulfill({ json: { id: `child-${submitted}`, state: 'succeeded',
      result: { answer: section, revision: revision(submitted === 1 ? 30 : 2) } } });
    return route.fulfill({ json: { id: 'parent', state: 'failed', failure: 'TIMED_OUT' } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('추가 질문', { exact: true }).fill('첫 보완 요청');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await page.getByText('보완 분석과 계산표 보기').click();
  await page.getByRole('slider', { name: '과거 성과 날짜 선택' }).fill('29');
  await page.getByLabel('추가 질문', { exact: true }).fill('두 번째 보완 요청');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect(page.locator('.followup-revision .history-date')).toHaveText('2026-10-02');
  expect(errors).toEqual([]);
});

test('asking opens a chat that keeps the conversation and returns to the same place in the analysis', async ({ page }) => {
  // 고객은 답을 받고 "그럼 …?" 하고 이어 묻는다. 대화는 한 화면에 쌓이고, 돌아가면 보던 분석 위치 그대로여야 한다.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'parent'));
  const turns: { id: string; state: string; input: { question: string }; created_at: string; result: unknown }[] = [];
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: turns } });
    if (path.endsWith('/chat')) {
      const turn = { id: `turn-${turns.length}`, state: 'running', input: { question: route.request().postDataJSON().question }, created_at: new Date().toISOString(), result: null };
      turns.push(turn); return route.fulfill({ status: 202, json: turn });
    }
    return route.fulfill({ json: { id: 'parent', state: 'succeeded', result: report } });
  });
  await page.goto('/portfolio/');
  await page.getByLabel('직접 물어보기', { exact: true }).scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => scrollY);
  await page.getByLabel('직접 물어보기', { exact: true }).fill('리밸런싱은 어떻게 해요?');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  const chat = page.getByRole('dialog', { name: '더 물어보기' });
  await expect(chat.locator('.chat-bubble--me')).toHaveText(['리밸런싱은 어떻게 해요?']);
  await expect(chat.locator('.chat-bubble--waiting')).toContainText('답을 쓰고 있어요');
  await expect(page.getByLabel('직접 물어보기', { exact: true })).toBeDisabled(); // 결과 화면에서도 답이 끝날 때까지 다음 질문을 막는다
  await expect(chat.getByRole('button', { name: '이어서 질문 보내기' })).toBeDisabled();
  Object.assign(turns[0], { state: 'succeeded', result: { answer: { ...section, paragraphs: ['나스닥100 비중을 30%로 줄여요.'] } } });
  await expect(chat.locator('.chat-bubble--ai')).toHaveText('나스닥100 비중을 30%로 줄여요.');
  await expect(chat).toContainText('4/5회 남음');
  await chat.getByRole('textbox', { name: '이어서 질문' }).fill('그럼 남는 돈은?');
  await chat.getByRole('textbox', { name: '이어서 질문' }).press('Enter');
  await expect(chat.locator('.chat-bubble--me')).toHaveText(['리밸런싱은 어떻게 해요?', '그럼 남는 돈은?']);
  await expect(chat).toContainText('3/5회 남음');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await chat.getByRole('button', { name: '분석 결과로 돌아가기' }).click();
  await expect(chat).toBeHidden();
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(before);
  await page.getByRole('button', { name: '이 분석에서 나눈 대화 보기 · 2개 · 답을 쓰는 중' }).click();
  await expect(chat).toBeVisible();
  await page.goBack();
  await expect(chat).toBeHidden();
  await expect(page.locator('[data-analytics-section=ask]')).toBeVisible();
});

test('a failed answer is not counted and the same question can be sent again from the chat', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'parent'));
  const sent: string[] = [];
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: [
      { id: 'failed', state: 'failed', input: { question: '메타를 많이 담은 ETF는?' }, result: null },
      ...sent.map((question, i) => ({ id: `again-${i}`, state: 'queued', input: { question }, result: null }))] } });
    if (path.endsWith('/chat')) { sent.push(route.request().postDataJSON().question); return route.fulfill({ status: 202, json: { id: `again-${sent.length - 1}`, state: 'queued' } }); }
    return route.fulfill({ json: { id: 'parent', state: 'succeeded', result: report } });
  });
  await page.goto('/portfolio/');
  // 키보드만으로도 대화를 다시 열 수 있어야 한다.
  await page.getByRole('button', { name: '이 분석에서 나눈 대화 보기 · 1개' }).focus();
  await page.keyboard.press('Enter');
  const chat = page.getByRole('dialog', { name: '더 물어보기' });
  await expect(chat.locator('.chat-bubble--failed')).toContainText('이 질문은 횟수에서 빠졌어요.');
  await expect(chat).toContainText('5/5회 남음');
  await chat.getByRole('button', { name: '같은 질문 다시 보내기' }).click();
  await expect.poll(() => sent).toEqual(['메타를 많이 담은 ETF는?']);
  await expect(chat).toContainText('4/5회 남음');
});
