import { fillForm } from './fill-form.mjs';
// Explicit billable end-to-end verification against the running API and worker.
import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
await mkdir('test-results', { recursive: true });
try {
  await page.goto(process.env.PORTFOLIO_URL || 'http://localhost:5180/portfolio/');
  await fillForm(page);
  await expect(page.locator('.progress')).toBeVisible();
  const jobId = await page.evaluate(() => localStorage.getItem('edge-portfolio-job'));
  console.log('submitted', jobId);
  await page.reload();
  await expect(page.getByRole('heading', { name: '핵심 평가 정리' })).toBeVisible({ timeout: 330000 });
  console.log('report ready');
  const completed = await page.evaluate(async id => (await fetch('/api/portfolio/analyses/' + id)).json(), jobId);
  expect(completed.result.schema_version).toBe(3);
  const plan = completed.result.recommendation;
  const optimization = completed.result.optimization;
  if (plan) {
  const selected = optimization.candidates.find(c => c.candidate_id === optimization.selected_candidate_id);
  expect(selected).toBeTruthy();
  const actual = {};
  for (const row of plan.allocations) {
    expect(row.kind).toBe('etf');
    actual[row.instrument_id] = (actual[row.instrument_id] || 0) + row.weight_pct;
  }
  expect(actual).toEqual(Object.fromEntries(selected.allocations.map(row => [row.instrument_id, row.weight_pct])));
  expect(Object.values(actual).every(value => value % 5 === 0)).toBe(true);
  expect(plan.historical_performance?.interval).toBe('1d');
  expect(plan.historical_performance?.currency).toBe('KRW');
  expect(plan.historical_performance.points.length).toBeGreaterThanOrEqual(200);
  expect(plan.historical_performance.points[0].recommended).toBe(1000000);
  const ahead = plan.historical_performance.points.slice(1).map(point => point.recommended > point.current);
  expect(ahead.filter(Boolean).length / ahead.length).toBeGreaterThanOrEqual(.5);
  expect(ahead.slice(-10).every(Boolean)).toBe(true);
  expect(plan.dividend_yield_1y).not.toBeNull();
  expect(plan.expected_return_1y).not.toBeNull();
  await page.locator('.report-history').scrollIntoViewIfNeeded();
  expect((await page.locator('polyline.b').getAttribute('points')).split(' ').length).toBe(plan.historical_performance.points.length);
  expect(plan.allocations.reduce((sum, h) => sum + h.weight_pct, 0)).toBeCloseTo(100, 6);
  if (plan.total_krw !== null) expect(plan.allocations.reduce((sum, h) => sum + BigInt(h.amount_krw), 0n)).toBe(BigInt(plan.total_krw));
  await expect(page.locator('.evaluation__part h4').nth(0)).toHaveText('최종 투자 의견과 실행할 행동');
  await expect(page.locator('.evaluation__part h4').nth(1)).toHaveText('앞으로의 전망');
  await expect(page.getByRole('heading', { name: '계좌별 최종 투자 제안' })).toBeVisible();
  } else {
    expect(optimization.candidates).toEqual([]);
    expect(optimization.selected_candidate_id).toBeNull();
    await expect(page.locator('.portfolio-hero')).toHaveCount(0);
  }
  for (const insight of completed.result.insights) {
    expect(insight.content.paragraphs.length).toBeGreaterThanOrEqual(3);
    expect(insight.content.paragraphs.length).toBeLessThanOrEqual(5);
  }
  await writeFile('test-results/live-report-v3.json', JSON.stringify(completed.result, null, 2));
  await page.screenshot({ path: 'test-results/live-mobile.png', fullPage: true });
  await page.getByRole('textbox', { name: '추가 질문', exact: true }).fill('이 분석에서 지금 가장 먼저 확인할 것은 무엇인가요?');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect(page.locator('.chat-answer')).toBeVisible({ timeout: 120000 });
  console.log('follow-up ready');
  await page.getByRole('textbox', { name: '추가 질문', exact: true }).fill('두 번째 질문입니다');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect(page.getByRole('dialog').filter({ hasText: '아직 준비 중' })).toBeVisible();
  await page.getByRole('button', { name: '포트폴리오로 돌아가기' }).click();
  await expect(page.getByRole('textbox', { name: '추가 질문', exact: true })).toBeDisabled();
  await page.locator('.premium').click();
  await page.getByLabel('이메일 주소').fill('portfolio-test@example.com');
  await page.getByRole('button', { name: '리포트 신청하기' }).click();
  await expect(page.getByRole('dialog')).toContainText('신청을 접수했어요');
  await page.getByRole('button', { name: '닫기', exact: true }).click();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: 'test-results/live-desktop.png', fullPage: true });
  expect(errors).toEqual([]);
  const result = await page.evaluate(async id => (await fetch('/api/portfolio/analyses/' + id)).json(), jobId);
  const outlookQueries = result.result.aws_outlook?.queries || [];
  expect(outlookQueries.length).toBeLessThanOrEqual(3);
  expect(outlookQueries.every(q => q.purpose?.trim() && q.decision_question?.trim())).toBe(true);
  await writeFile('test-results/live-verification.json', JSON.stringify({ jobId, schemaVersion: result.result.schema_version, optimization, recommendationCount: plan?.allocations.length ?? 0, insightCount: completed.result.insights.length, expectedReturn: plan?.expected_return_1y, dividendYield: plan?.dividend_yield_1y, dailyPoints: plan?.historical_performance.points.length, elapsed: result.result.elapsed_seconds, sourceCount: result.result.sources.length, outlookQueries, states: ['submitted', 'restored', 'succeeded', 'chat_answered', 'chat_blocked', 'email_received'], errors }, null, 2));
  console.log('live end-to-end passed', result.result.elapsed_seconds);
} finally { await browser.close(); }
