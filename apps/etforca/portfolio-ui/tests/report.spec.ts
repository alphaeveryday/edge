import { test, expect, type Page } from '@playwright/test';
import { deepestFall, futureValue, legacyNote, won } from '../src/resultMath';

// 8차 결과(ResultView): 누구에게나 같은 일곱 칸, 같은 차례. 서버가 새 필드를 아직 내지 않은 옛 보고서도
// 기존 필드로 같은 칸을 채워야 한다(운영의 지난 보고서가 그대로 다시 열린다).
const section = (paragraphs: string[], evidence: { source_id: string; quote: string }[] = []) => ({ paragraphs, evidence, limitations: [] as string[] });
const row = (id: string, name: string, account: string, before: number, target: number) => ({ instrument_id: id, name, account, before_krw: String(before), target_krw: String(target),
  buy_krw: String(Math.max(0, target - before)), sell_krw: String(Math.max(0, before - target)), cash_used_krw: '0' });
const evaluation = (overall: number | null, scores: Record<string, number> | null, reason = section(['평가 이유'])) => ({ overall, scores, reason, method: 'AI 정성 평가', rubric_version: 1 });
// 1,000,000 에서 시작하는 옛 기록(initial_krw 100만 원) — 화면은 고객의 실제 금액(1,250만 원)에 맞춰 늘려 보여야 한다.
const points = [['2022-10-24', 1000000, 1000000], ['2023-10-24', 800000, 700000], ['2024-10-24', 1300000, 1100000], ['2025-10-22', 1500000, 1200000]]
  .map(([date, recommended, current]) => ({ date, recommended, current }));
const base = () => ({
  schema_version: 3, as_of: '2026-10-23T03:00:00Z', model: 'x', elapsed_seconds: 1,
  sources: [{ id: 's1', url: 'https://example.com/a', fetched_at: '2026-10-23' }],
  sections: { question: section(['반도체 ETF만 S&P500으로 옮기세요.']), portfolio: section(['포트폴리오 이유']), outlook: section(['전망 문단']),
    opinion: section(['##### 판 날 같은 계좌에서 사세요.', '| 종목 | 비중 |']) },
  current_evaluation: evaluation(3.2, { leadership: 3.5, profitability: 3.5, stability: 2.5 }, section(['지금 구성 이유'], [{ source_id: 's1', quote: '근거 문장' }])),
  metrics: { total_krw: '12500000', holdings: [{ name: 'TIGER 미국S&P500', account: 'ISA', weight_pct: 40 }], unavailable_reason: '' },
  optimization: { status: 'available', selected_candidate_id: 'c1' },
  recommendation: { title: '반도체만 바꾸기', total_krw: '12500000', unavailable_reason: '', expected_return_1y: null, dividend_yield_1y: null, actions: [],
    evaluation: evaluation(4.4, { leadership: 4.5, profitability: 4.5, stability: 4.2 }, section(['추천 이유'])),
    allocations: [{ instrument_id: 'KR:360750', name: 'TIGER 미국S&P500', ticker: '360750', kind: 'etf', account: 'ISA', weight_pct: 64, amount_krw: '8000000' },
      { instrument_id: 'KR:360750', name: 'TIGER 미국S&P500', ticker: '360750', kind: 'etf', account: '일반 계좌', weight_pct: 20, amount_krw: '2500000' },
      { instrument_id: 'KR:005930', name: '삼성전자', ticker: '005930', kind: 'stock', account: '일반 계좌', weight_pct: 16, amount_krw: '2000000' }],
    historical_performance: { basis: '수정 종가 기준 분배금 재투자', source_url: '', interval: '1d', currency: 'KRW', initial_krw: 1000000, baseline_kind: 'current_holdings', points },
    execution: { status: 'ready', reasons: [], assumptions: [], additional_krw: '0', monthly_krw: '300000', required_additional_krw: null, months: null, accounts: [],
      rows: [row('KR:360750', 'TIGER 미국S&P500', 'ISA', 8000000, 8000000), row('KR:446770', 'ACE 글로벌반도체TOP4 Plus', '일반 계좌', 2500000, 0),
        row('KR:360750', 'TIGER 미국S&P500', '일반 계좌', 0, 2500000), row('KR:005930', '삼성전자', '일반 계좌', 2000000, 2000000)] } },
  insights: [{ title: '겹치는 종목이 많아요', content: section(['S&P500과 나스닥100은 애플이 겹쳐요.']) }],
} as any);

// 화면과 떼어 둔 금융 계산(resultMath). 고객이 읽는 금액이 틀리면 화면이 맞게 그려져도 소용없다.
test('money words, deepest fall, five-percent projection and account transfers are computed exactly', () => {
  expect([won(0), won(9999), won(10000), won(7675920), won(100000000), won(127800000), won(-2610000), won(25551824, true)])
    .toEqual(['0원', '9,999원', '1만 원', '767만 5,920원', '1억 원', '1억 2,780만 원', '-261만 원', '2,555만 원']);
  // 금액과 비율은 같은 구간에서: 100→50(50만 원, 50%) 뒤 300→200(100만 원, 33.3%)이면 가장 많이 줄어든 돈은 뒤 구간.
  const fall = deepestFall([1000000, 500000, 3000000, 2000000]);
  expect(fall.amount).toBe(1000000);
  expect(fall.ratio).toBeCloseTo(1 / 3);
  // 연 5% 복리: 1,000만 원은 15년 뒤 약 2,079만 원, 매달 넣는 돈만 있으면 넣은 돈보다 커진다.
  expect(Math.round(futureValue(10000000, 0, 15))).toBe(20789282);
  expect(futureValue(0, 300000, 15)).toBeGreaterThan(300000 * 12 * 15);
  // 한 계좌에서 판 돈은 다른 계좌의 매수 대금이 되지 않는다 — 옮길 돈을 밝힌다.
  expect(legacyNote({ status: 'ready', reasons: [], assumptions: [], additional_krw: '0', monthly_krw: null, required_additional_krw: null, months: null, rows: [],
    accounts: [{ account: '일반 계좌', deposit_krw: '0', withdrawal_krw: '3000000' }, { account: 'ISA', deposit_krw: '3000000', withdrawal_krw: '0' }] }))
    .toBe('계좌 사이 옮길 돈: 일반 계좌에서 300만 원 빼기, ISA에 300만 원 넣기.');
  // 확인할 것이 남은 옛 추천은 그 조건을 먼저 보인다(예전 ExecutionDetails 가 하던 일).
  expect(legacyNote({ status: 'needs_input', reasons: ['ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED:ISA'], assumptions: [], additional_krw: '0', monthly_krw: null, required_additional_krw: null, months: null, rows: [], accounts: [] }))
    .toBe('실행하기 전에 확인할 것이 있어요. ISA · 금융사에서 남은 납입 한도를 확인해야 해요.');
});

async function open(page: Page, result: unknown, input: unknown = { question: '이 구성으로 계속 들고 가도 돼요?', horizon: '10년 이상', holdings: [] }) {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'r8'));
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: [] } });
    if (path.endsWith('/analyses/r8')) return route.fulfill({ json: { id: 'r8', state: 'succeeded', input, result } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await expect(page.locator('.r8-hero')).toBeVisible();
}
const sections = (page: Page) => page.locator('.r8 > [data-analytics-section]').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-analytics-section')));

test('every customer gets the same seven cells in the same order, then the survey', async ({ page }) => {
  await open(page, base());
  // 칸 이름은 PostHog 칸별 체류 집계(계획 '측정' 표)의 열쇠라 바뀌면 전후 비교가 끊긴다.
  expect(await sections(page)).toEqual(['answer', 'final', 'monthly', 'past', 'future', 'analysis', 'ask', 'survey']);
  // 만족도 팝업·입력 보유 저장·면책 문구·겹침 통찰은 결과에서 빠졌다(사용자 결정).
  await expect(page.locator('.report-feedback, .save-portfolio, .feedback-anchor')).toHaveCount(0);
  await expect(page.locator('.r8')).not.toContainText('겹치는 종목');
  await expect(page.locator('.r8')).not.toContainText('#####');
  await expect(page.locator('.r8')).not.toContainText('| 종목');
  await expect(page.locator('.r8')).not.toContainText('NaN');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/result-v8-${test.info().project.name}.png`, fullPage: true });
});

test('an old report without verdict fields still answers, recommends and compares in money', async ({ page }) => {
  await open(page, base());
  const hero = page.locator('.r8-hero');
  await expect(hero.locator('.r8-score-value b')).toHaveText('3.2');
  await expect(hero.locator('.r8-grades [data-on]')).toHaveText('보통');
  await expect(hero.locator('q')).toHaveText('이 구성으로 계속 들고 가도 돼요?');
  // 판정은 실행표에서 정한다: 바뀌는 종목이 둘(반도체 팔고 S&P500 사기)이면 '다시 짜야 해요'.
  await expect(hero.locator('.r8-headline')).toHaveText('다시 짜야 해요');
  await expect(hero.locator('.r8-answer')).toHaveText('반도체 ETF만 S&P500으로 옮기세요.');
  await expect(hero.locator('.r8-meta')).toHaveText('10월 23일 기준 · 종목 3개 · 1,250만 원 · 매달 30만 원');

  const final = page.locator('[data-analytics-section=final]');
  await expect(final.locator('.r8-rating b')).toHaveText('4.4');
  const sold = final.locator('.r8-final-row').filter({ hasText: 'ACE' });
  await expect(sold).toHaveAttribute('data-gone', 'true');
  await expect(sold.locator('.r8-change')).toHaveText('250만 원→0원전부 팔기');
  await expect(final.locator('.r8-final-row').filter({ hasText: '0원→' }).locator('.r8-change b')).toHaveText('250만 원');
  await expect(final.locator('.r8-total b')).toHaveText('1,250만 원');
  // 매수는 빨강, 매도는 파랑(디자인 시스템의 금융 색 규칙).
  await expect(final.locator('.r8-change b[data-tone=buy]')).toHaveCSS('color', 'rgb(210, 32, 48)');
  await expect(final.locator('.r8-change b[data-tone=sell]')).toHaveCSS('color', 'rgb(27, 100, 218)');

  // 월 납입 배분(monthly_plan)이 없는 옛 보고서는 같은 칸에 비율로.
  await expect(page.locator('[data-analytics-section=monthly] h2')).toHaveText('매달 30만 원은 이 비율로');
  await expect(page.locator('[data-analytics-section=monthly] .r8-row-value')).toHaveText(['76.2%', '23.8%']);

  // 100만 원 기준 기록을 고객 금액 1,250만 원에 맞춘다: 추천 1,875만 원, 지금 그대로 1,500만 원 → 375만 원 더.
  const past = page.locator('[data-analytics-section=past]');
  await expect(past.locator('.r8-eyebrow').first()).toHaveText('지난 3년, 이렇게 했다면');
  await expect(past.locator('h2')).toHaveText('375만 원 더 벌었어요');
  // 가장 크게 떨어졌을 때는 고점에서 저점까지 실제로 줄어든 돈: 지금 그대로 1,250만→875만 -375만 원(-30%, 더 잃는 쪽만 빨강),
  // 추천 1,250만→1,000만 -250만 원(-20%). 다음 고점 뒤 낙폭이 아니라 가장 깊은 낙폭이어야 한다.
  await expect(past.locator('.r8-loss-box b')).toHaveText(['-375만 원', '-250만 원']);
  await expect(past.locator('.r8-loss-box span:last-child')).toHaveText(['-30.0%', '-20.0%']);
  await expect(past.locator('.r8-loss-box[data-worse]')).toContainText('지금 그대로');
  await expect(past.locator('.r8-strong')).toHaveText('추천이 125만 원 덜 잃었어요');

  // 앞으로 내 돈: 1,250만 원 + 매달 30만 원, 연 5% 가정을 칸에 적는다.
  const future = page.locator('[data-analytics-section=future]');
  await expect(future).toContainText('연 5%');
  await expect(future.locator('.r8-bar-col')).toHaveCount(3);

  const analysis = page.locator('[data-analytics-section=analysis]');
  await expect(analysis.locator('h3').first()).toHaveText('왜 3.2점이에요?');
  await expect(analysis.locator('.r8-score-line').first()).toContainText('3.5');
  await expect(analysis.locator('.r8-score-line').first()).toContainText('4.5');
  await expect(page.locator('[data-analytics-section=ask] .r8-ask-row')).toHaveCount(4);
});

test('an old report with several questions keeps every answer', async ({ page }) => {
  // 예전 결과는 질문마다 답을 보였다. 맨 위 답 칸에는 첫 답만 들어가므로 나머지는 오르카의 분석에 남아야 한다.
  await open(page, { ...base(), completion: { status: 'partial', missing_inputs: [], missing_research: [], tasks: [
    { question: '첫 질문은요?', status: 'answered', answer: '첫 답이에요.', missing: [] },
    { question: '두 번째 질문은요?', status: 'answered', answer: '두 번째 답이에요.', missing: [] },
    { question: '세 번째 질문은요?', status: 'partial', answer: '', missing: ['ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED:ISA', '남은 납입 한도를 알려 주세요.'] }] } });
  await expect(page.locator('.r8-answer')).toHaveText('첫 답이에요.');
  const analysis = page.locator('[data-analytics-section=analysis]');
  await expect(analysis.locator('h3')).toContainText(['두 번째 질문은요?', '세 번째 질문은요?']);
  await expect(analysis).toContainText('두 번째 답이에요.');
  await expect(analysis).not.toContainText('첫 답이에요.');
  await expect(analysis).not.toContainText('ACCOUNT_CONTRIBUTION');
});

test('new server fields take priority over the fallbacks', async ({ page }) => {
  const result = { ...base(), verdict: { kind: 'one', headline: '하나만 바꾸면 돼요' }, answer: '반도체 ETF 250만 원만 옮기세요. 지난 3년이었다면 287만 원을 더 벌었어요.',
    constraint_results: [{ kind: 'no_leverage', status: 'kept', note: '' }, { kind: 'no_sell', status: 'substituted', note: '월 납입으로 줄여요' }],
    final_note: '판 날 같은 계좌에서 바로 사세요.', period: { start: '2025-06-23', end: '2025-10-22', label: '상장 뒤 4개월' },
    past: { rec_end_krw: '15370000', cur_end_krw: '14500000', rec_max_loss_krw: '2050000', cur_max_loss_krw: '2610000' },
    analysis: [{ title: '왜 반도체 ETF만 바꿔요?', paragraphs: ['반도체 ETF는 크게 오르내려요.'], evidence: [{ source_id: 's1', quote: '반도체 -38%' }], limitations: [] }],
    suggested_questions: ['채권을 조금 넣으면?', '나스닥100 하나만 사면?', '조금 더 공격적으로', '다시 바꿀 때는 언제?'] };
  result.recommendation.execution.monthly_plan = [{ account: 'ISA', instrument_id: 'KR:360750', krw: '200000' }, { account: 'ISA', instrument_id: 'KR:379810', krw: '100000' }];
  await open(page, result);
  await expect(page.locator('.r8-headline [data-tone=warn]')).toHaveText('하나만 바꾸면 돼요');
  await expect(page.locator('.r8-answer')).toHaveText(result.answer);
  await expect(page.locator('.r8-final .r8-badge[data-tone=success]')).toHaveText('레버리지 빼기 · 지켰어요');
  await expect(page.locator('.r8-final .r8-badge[data-tone=warning]')).toHaveText('갖고 있는 건 팔지 않기 · 대신했어요');
  await expect(page.locator('.r8-callout')).toHaveText('판 날 같은 계좌에서 바로 사세요.');
  await expect(page.locator('[data-analytics-section=monthly] h2')).toHaveText('매달 30만 원은 여기에');
  await expect(page.locator('[data-analytics-section=monthly] .r8-row-value')).toHaveText(['20만 원', '10만 원']);
  await expect(page.getByRole('button', { name: /매달 넣는 날 알림 받기/ })).toBeVisible();
  // 기록이 짧으면 머리말이 기간을 숨기지 않는다(계획 '위험: 짧은 기간').
  await expect(page.locator('[data-analytics-section=past] .r8-eyebrow').first()).toHaveText('상장 뒤 4개월, 이렇게 했다면');
  await expect(page.locator('[data-analytics-section=past] h2')).toHaveText('87만 원 더 벌었어요');
  // 요약(past)의 기간과 다른 옛 기록으로는 그래프를 그리지 않는다 — 제목과 그래프 숫자가 어긋나면 안 된다.
  await expect(page.locator('.r8-past-chart')).toHaveCount(0);
  await expect(page.locator('[data-analytics-section=analysis] h3')).toHaveText(['왜 반도체 ETF만 바꿔요?']);
  await expect(page.locator('.r8-ask-row')).toHaveText(result.suggested_questions);
});

test('an old multi-month plan says its target includes money still to be added', async ({ page }) => {
  // 8차 전 실행표는 추가 자금·여러 달 납입을 목표 금액에 넣었다. 안내가 없으면 목표 금액을 지금 할 거래로 읽는다.
  const result = base();
  Object.assign(result.recommendation.execution, { status: 'conditional', additional_krw: '3000000', monthly_krw: '500000', months: 6 });
  await open(page, result);
  await expect(page.locator('.r8-final .r8-callout')).toHaveText('추천 금액에는 새로 넣을 300만 원이 들어 있어요. 매달 50만 원씩 6개월 넣어 맞추는 계획이에요.');
});

test('adding money in the same proportions is not a rebuild', async ({ page }) => {
  // 옛 실행표는 돈을 더 넣으면 모든 종목 금액이 늘었다. 비중이 같으면 '이대로 가도 돼요'여야 한다.
  const result = base();
  result.recommendation.execution.rows = [row('KR:360750', 'TIGER 미국S&P500', 'ISA', 5000000, 5500000), row('KR:379810', 'KODEX 미국나스닥100', 'ISA', 5000000, 5500000)];
  await open(page, result);
  await expect(page.locator('.r8-headline')).toHaveText('이대로 가도 돼요');
});

test('selling part of a holding into cash is a change, not keep', async ({ page }) => {
  // 현금으로 돌리라는 계획을 '이대로 가도 돼요'로 말하면 실행표와 판정이 모순된다.
  const result = base();
  result.recommendation.execution.rows = [row('KR:360750', 'TIGER 미국S&P500', 'ISA', 1000000, 500000), row('CASH:KRW', '현금', 'ISA', 0, 500000)];
  await open(page, result);
  await expect(page.locator('.r8-headline')).toHaveText('하나만 바꾸면 돼요');
});

test('a first-time investor sees Orca instead of a score and compares with cash', async ({ page }) => {
  const result = base();
  result.current_evaluation = evaluation(null, null);
  result.recommendation.historical_performance.baseline_kind = 'new_cash';
  result.recommendation.execution.rows = [row('CASH:KRW', '현금', 'ISA', 12500000, 0), row('KR:360750', 'TIGER 미국S&P500', 'ISA', 0, 12500000)];
  await open(page, result, { question: '', horizon: '10년 이상', holdings: [{ name: '현금', account: 'ISA', amount_krw: '12500000', kind: 'cash', instrument_id: 'CASH:KRW' }] });
  await expect(page.locator('.r8-hero-orca')).toBeVisible();
  await expect(page.locator('.r8-score')).toHaveCount(0);
  await expect(page.locator('.r8-headline')).toHaveText('이렇게 시작하세요');
  await expect(page.locator('.r8-hero q')).toHaveText('이대로 가도 될까요?');
  await expect(page.locator('.r8-meta')).toContainText('처음 시작');
  await expect(page.locator('[data-analytics-section=past] h2')).toHaveText('현금보다 625만 원 더 벌었어요');
  await expect(page.locator('[data-analytics-section=past] .r8-loss-box').first()).toContainText('현금');
});

test('weights without amounts stay percentages and never invent money', async ({ page }) => {
  const result = base();
  result.metrics.total_krw = null; result.recommendation.total_krw = null;
  result.recommendation.execution = undefined;
  result.recommendation.allocations.forEach((a: any) => { a.amount_krw = null; });
  await open(page, result);
  await expect(page.locator('.r8-final .r8-row-value')).toHaveText(['64%', '20%', '16%']);
  await expect(page.locator('.r8-final .r8-total')).toHaveCount(0);
  await expect(page.locator('[data-analytics-section=past]')).toHaveCount(0);
  await expect(page.locator('[data-analytics-section=future]')).toHaveCount(0);
  await expect(page.locator('.r8')).not.toContainText('NaN');
});

test('a withheld recommendation still shows the final cell with the current holdings and the reason', async ({ page }) => {
  await open(page, { ...base(), recommendation: null, optimization: { status: 'available', reason: 'CUSTOMER_CONSTRAINT_NOT_SATISFIED', selected_candidate_id: null } });
  await expect(page.locator('.r8-headline')).toHaveText('이번에는 추천을 만들지 못했어요');
  await expect(page.locator('[data-analytics-section=final]')).toContainText('TIGER 미국S&P500');
  await expect(page.locator('.r8-callout')).toContainText('고르신 조건을 모두 지키는 구성을 찾지 못했어요.');
  expect(await sections(page)).toEqual(['answer', 'final', 'analysis', 'ask', 'survey']);
});

test('very large amounts and enlarged text do not overflow the phone width', async ({ page }) => {
  const result = base();
  result.recommendation.execution.rows[0] = row('KR:360750', 'TIGER 미국S&P500', 'ISA', 1234567890123, 1234567890123);
  await open(page, result);
  const size = () => page.locator('.r8-answer').evaluate(el => parseFloat(getComputedStyle(el).fontSize));
  const before = await size();
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  // 고객이 글자를 키우면 결과 글도 커져야 한다(고정 px 이면 그대로라 확대가 무시된다).
  expect(await size()).toBeGreaterThan(before * 1.5);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator('.r8-final-row').first().locator('.r8-row-value')).toHaveText('12,345억 6,789만 123원');
});

test('a broken saved survey step falls back to the first question instead of breaking the result', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-survey:r8', 'constructor'));
  await open(page, base());
  await expect(page.locator('[data-analytics-section=survey] h3')).toContainText('얼마나 반영했나요?');
});

test('the survey asks one question at a time, asks why only when worry grew, and never returns', async ({ page }) => {
  await open(page, base());
  const survey = page.locator('[data-analytics-section=survey]');
  await survey.getByRole('button', { name: '어느 정도 반영했어요' }).click();
  await expect(survey.locator('h3')).toContainText('고민은 어떻게 달라졌나요?');
  await expect(survey.locator('h3')).toBeFocused();
  await survey.getByRole('button', { name: '늘었어요' }).click();
  await expect(survey.locator('h3')).toHaveText('어떤 점 때문에 고민이 늘었나요?');
  await survey.getByRole('button', { name: '몰랐던 문제를 알게 됐어요' }).click();
  await expect(survey.getByRole('button', { name: '몰랐던 문제를 알게 됐어요' })).toHaveAttribute('aria-pressed', 'true');
  await survey.getByRole('button', { name: '보내기' }).click();
  await expect(survey).toContainText('답해 주셔서 고마워요');
  // 분석 하나에 한 번: 다시 열면 설문 칸이 없다.
  await page.reload();
  await expect(page.locator('.r8-hero')).toBeVisible();
  await expect(page.locator('[data-analytics-section=survey]')).toHaveCount(0);
});

test('a suggested question opens the chat and sends it as the customer question', async ({ page }) => {
  const sent: string[] = [];
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'r8'));
  await page.route('**/api/portfolio/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/chat')) { sent.push(route.request().postDataJSON().question); return route.fulfill({ json: { id: 'c1', state: 'queued' } }); }
    if (path.endsWith('/chats')) return route.fulfill({ json: { limit: 5, items: [] } });
    if (path.endsWith('/analyses/r8')) return route.fulfill({ json: { id: 'r8', state: 'succeeded', input: { question: 'q', horizon: '', holdings: [] }, result: { ...base(), suggested_questions: ['채권을 조금 넣으면?', 'b', 'c', 'd'] } } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/portfolio/');
  await page.getByRole('button', { name: '채권을 조금 넣으면?' }).click();
  await expect(page.locator('dialog.chat-screen')).toBeVisible();
  expect(sent).toEqual(['채권을 조금 넣으면?']);
});

test('the type card row appears only when the server picked a known type', async ({ page }) => {
  await open(page, { ...base(), type_card: { key: 'lev', stats: [['레버리지', '45%'], ['주로 담은 것', '나스닥 2배']] } });
  await page.getByRole('button', { name: /내 투자 유형 카드 만들기/ }).click();
  const sheet = page.locator('dialog.r8-type-sheet');
  await expect(sheet.locator('.r8-type-name b')).toHaveText('레버리지 질주형');
  await expect(sheet.locator('.r8-type-stats')).toContainText('45%');
  // 금액은 카드에 나오지 않는다.
  await expect(sheet.locator('.r8-type-card')).not.toContainText('원');
  await sheet.getByRole('button', { name: '유형 카드 닫기' }).click();
  await expect(sheet).toBeHidden();
  await page.unrouteAll({ behavior: 'wait' });
  await open(page, { ...base(), type_card: { key: 'unknown-type' } });
  await expect(page.getByRole('button', { name: /내 투자 유형 카드 만들기/ })).toHaveCount(0);
});

test('interrupted analysis exposes saved work without publishing its draft recommendation', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'interrupted'));
  await page.route('**/api/portfolio/analyses/interrupted', route => route.fulfill({ json: {
    id: 'interrupted', state: 'failed', failure: 'WORKER_INTERRUPTED', result: { partial_report: { ...base(),
      completion: { status: 'partial', tasks: [{ question: '매수 금액은 얼마인가요?', status: 'partial', answer: '납입 가능 금액을 확인하고 있어요.', missing: [] }],
        missing_research: [], missing_inputs: ['현재 납입 가능 금액을 확인해야 해요.'] } } },
  } }));
  await page.goto('/portfolio/');
  await expect(page.getByRole('heading', { name: /끝내지 못했어요/ })).toBeVisible();
  await expect(page.getByText('횟수는 줄지 않았어요')).toBeVisible();
  await page.getByText('멈추기 전까지 본 것').click();
  await expect(page.locator('.completion-summary')).toContainText('매수 금액은 얼마인가요?');
  await expect(page.locator('.completion-summary')).toContainText('현재 납입 가능 금액을 확인해야 해요.');
  await expect(page.locator('.portfolio-hero')).toHaveCount(0);
});
