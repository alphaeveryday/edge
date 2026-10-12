import { test, expect } from '@playwright/test';

test('default account, real stages, result restoration, chat gate and email receipt', async ({ page }) => {
  let ready = false;
  const section = { paragraphs: ['검증용 포트폴리오 설명이에요.', '검증용 판단 조건이에요.'], evidence: [], limitations: [] };
  await page.route('**/api/portfolio/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/chat')) return route.fulfill({ status: 409, json: { detail: 'CHAT_LIMIT' } });
    if (path.endsWith('/report-request')) return route.fulfill({ status: 201, json: { status: 'received' } });
    if (path.endsWith('/session')) return route.fulfill({ json: {} });
    const result = { sections: { question: section, portfolio: section, outlook: section, opinion: section }, sources: [], as_of: '2026-10-12T00:00:00Z', metrics: { total_krw: '1000000', holdings: [{ name: 'VOO', account: '일반 계좌', weight_pct: 100 }], unavailable_reason: '시계열 미연결' } };
    return route.fulfill({ json: { id: 'test-job', state: ready ? 'succeeded' : 'running', stage: 'portfolio', started_at: new Date().toISOString(), result: ready ? result : null } });
  });
  await page.goto('/portfolio/');
  await expect(page.getByLabel('투자 계좌 1')).toHaveValue('일반 계좌');
  await page.getByLabel('ETF 이름 또는 종목 코드').fill('VOO');
  await page.getByLabel('현재 평가금액').fill('1000000');
  await page.getByRole('button', { name: '내 포트폴리오 분석하기' }).click();
  await expect(page.locator('.progress')).toContainText('약 5분');
  await expect(page.locator('.active')).toContainText('현재 포트폴리오 분석');
  ready = true;
  await page.reload();
  await expect(page.getByRole('heading', { name: '핵심 평가 정리' })).toBeVisible();
  await expect(page.locator('.evaluation section')).toHaveCount(4);
  await expect(page.getByRole('heading', { name: '과거 성과' })).toBeVisible();
  await page.getByLabel('추가 질문', { exact: true }).fill('다시 물어볼게요');
  await page.getByRole('button', { name: '질문 보내기' }).click();
  await expect(page.getByRole('dialog').filter({ hasText: '아직 준비 중' })).toBeVisible();
  await page.getByRole('button', { name: '포트폴리오로 돌아가기' }).click();
  await expect(page.getByRole('textbox', { name: '추가 질문', exact: true })).toBeDisabled();
  await page.locator('.premium').click();
  await page.getByLabel('이메일 주소').fill('test@example.com');
  await page.getByRole('button', { name: '리포트 신청하기' }).click();
  await expect(page.getByRole('dialog')).toContainText('신청을 접수했어요');
  await expect(page.getByRole('dialog')).toContainText('발송 일정은 아직');
  await page.getByRole('button', { name: '닫기', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/report-${test.info().project.name}.png`, fullPage: true });
});
