import { test, expect } from '@playwright/test';

test('version history preserves failed attempts and shows all 20 cases without borrowing another version', async ({ page }) => {
  let disconnected = false;
  let listRequests = 0;
  const cases = Array.from({ length: 20 }, (_, i) => ({ case: i + 1, title: `유형 ${i + 1}`, input: { question: `질문 ${i + 1}` }, expected: '명료한 결론' }));
  const runs = [{ id: 'new', version: 'new-commit', environment: 'production_runtime', created_at: '2026-10-16T01:00:00Z' }, { id: 'old', version: 'old-commit', environment: 'isolated_runtime', created_at: '2026-10-15T01:00:00Z' }];
  const attempts = [{ case_id: 1, attempt: 1, state: 'failed', failures: ['MODEL_TIMEOUT'], seconds: 600 }, { case_id: 1, attempt: 2, state: 'succeeded', failures: ['추천 비중 없음'], seconds: 50 }];
  const section = { paragraphs: ['재시도 결론 본문'], limitations: [], evidence: [] };
  const result = { sections: { opinion: section, outlook: section, question: section, portfolio: section }, metrics: { holdings: [] }, sources: [], insights: [] };
  await page.route('**/api/portfolio/admin/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (disconnected) return route.fulfill({ status: 503, json: {} });
    if (path.endsWith('/overview')) return route.fulfill({ json: { as_of: new Date().toISOString(), today_jobs: {}, daily: [] } });
    if (path.endsWith('/records')) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 50 } });
    if (path.endsWith('/verification-runs')) { listRequests++; return route.fulfill({ json: runs }); }
    if (path.endsWith('/old/1/1')) return route.fulfill({ json: { ...attempts[0], result: null } });
    if (path.endsWith('/old/1/2')) return route.fulfill({ json: { ...attempts[1], input: { question: '재시도 당시 수정된 질문' }, result } });
    const old = path.endsWith('/old');
    return route.fulfill({ json: { ...runs[old ? 1 : 0], cases, attempts: old ? attempts : [] } });
  });
  await page.goto('/portfolio/admin.html');
  await page.getByRole('button', { name: '버전별 검증' }).click();
  await expect(page.locator('.verification tbody tr')).toHaveCount(20);
  await expect(page.getByText('미실행', { exact: true })).toHaveCount(20);
  const before = listRequests;
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await expect.poll(() => listRequests).toBeGreaterThan(before);
  await page.getByLabel('검증 버전', { exact: true }).selectOption('old');
  await page.getByRole('button', { name: '1번 · 1차 · 실행 실패', exact: true }).click();
  await expect(page.getByRole('region', { name: '검증 입력과 답변' })).toContainText('MODEL_TIMEOUT');
  await page.getByRole('button', { name: '1번 · 2차 · 자동검사 실패', exact: true }).click();
  await expect(page.getByRole('region', { name: '검증 입력과 답변' })).toContainText('추천 비중 없음');
  await expect(page.getByText('재시도 결론 본문', { exact: true }).first()).toBeVisible();
  await page.getByText('실행 당시 입력 전체', { exact: true }).click();
  await expect(page.getByText('재시도 당시 수정된 질문', { exact: true })).toBeVisible();
  await page.getByLabel('검증 버전', { exact: true }).selectOption('new');
  await expect(page.getByRole('region', { name: '검증 입력과 답변' })).toHaveCount(0);
  await expect(page.getByText('미실행', { exact: true })).toHaveCount(20);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  disconnected = true;
  await page.getByLabel('검증 버전', { exact: true }).selectOption('old');
  await expect(page.getByRole('heading', { name: '연결이 끊겼어요' })).toBeVisible();
  await expect(page.getByText('old-commit')).toHaveCount(0);
});
