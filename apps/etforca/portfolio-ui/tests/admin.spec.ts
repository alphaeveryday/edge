import { test, expect } from '@playwright/test';

test('customer requests and metrics stay separate from the test tab', async ({ page }) => {
  const queries: string[] = [];
  await page.route('**/api/portfolio/admin/**', route => {
    const url = new URL(route.request().url());
    const scope = url.searchParams.get('scope');
    queries.push(url.pathname + url.search);
    const items = [
      { id: 'customer', visitor: 'customer', at: new Date().toISOString(), state: 'succeeded' },
      { id: 'test', visitor: 'test', at: new Date().toISOString(), state: 'succeeded', test_label: '20 cases / 01' },
    ].filter(row => scope === 'customers' ? !row.test_label : scope === 'tests' ? row.test_label : true);
    if (url.pathname.endsWith('/overview')) return route.fulfill({ json: { as_of: new Date().toISOString(), queued: 0, running: 1, today_jobs: { succeeded: 1 }, daily: [{ day: '2026-10-17', visitors: 3, analyses: 1 }] } });
    return route.fulfill({ json: { total: items.length, page: 1, page_size: 50, items } });
  });
  await page.goto('/portfolio/admin.html');
  await expect(page.locator('.records tbody tr')).toHaveCount(1);
  await expect(page.locator('.bar-pair span').first()).toHaveCSS('background-color', 'rgb(49, 130, 246)');
  await expect(page.locator('.bar-pair span').last()).toHaveCSS('background-color', 'rgb(107, 118, 132)');
  await expect(page.locator('.trend i').first()).toHaveCSS('background-color', 'rgb(49, 130, 246)');
  await expect(page.locator('.trend i').last()).toHaveCSS('background-color', 'rgb(107, 118, 132)');
  await expect(page.locator('.records .badge.test')).toHaveCount(0);
  await expect(page.locator('.stat').filter({ hasText: '오늘 분석 요청' }).locator('strong')).toHaveText('1');
  expect(queries.some(q => q.endsWith('/overview'))).toBeTruthy();
  expect(queries.some(q => q.includes('/records?') && q.includes('scope=customers'))).toBeTruthy();
  await page.getByRole('button', { name: '테스트 분석' }).click();
  await expect(page.locator('.records .badge.test')).toHaveText('테스트');
  await expect(page.locator('.stat').filter({ hasText: '오늘 분석 요청' }).locator('strong')).toHaveText('1');
  await page.getByRole('button', { name: '분석 요청' }).click();
  await expect(page.locator('.records .badge.test')).toHaveCount(0);
  await expect(page.locator('.records .visitor')).toHaveText('customer');
  await expect(page.locator('.stats small')).toHaveCount(0);
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  expect(await page.locator('.filters select').evaluateAll(fields => fields.every(field => {
    const bounds = field.closest('.records')!.getBoundingClientRect(), box = field.getBoundingClientRect();
    return box.left >= bounds.left && box.right <= bounds.right;
  }))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  if (test.info().project.name === 'desktop') {
    await page.setViewportSize({ width: 1280, height: 500 });
    const lastTab = page.getByRole('button', { name: /^버전별 검증/ });
    await lastTab.scrollIntoViewIfNeeded();
    expect(await lastTab.evaluate(el => { const box = el.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight; })).toBe(true);
  }
});

test('SSM dashboard detail, tabs, refresh and disconnected-data clearing', async ({ page }) => {
  let loggedIn = true;
  let expired = false;
  let count = 2;
  let listQueries: string[] = [];
  await page.route('**/api/portfolio/admin/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/login')) { loggedIn = true; return route.fulfill({ json: { authenticated: true } }); }
    if (path.endsWith('/logout')) { loggedIn = false; return route.fulfill({ json: {} }); }
    if (!loggedIn || expired) return route.fulfill({ status: 401, json: { detail: 'ADMIN_LOGIN_REQUIRED' } });
    if (path.endsWith('/overview')) return route.fulfill({ json: { as_of: new Date().toISOString(), active_visitors: count, today_visitors: 12, queued: 1, running: 1, today_jobs: { queued: 1, running: 1, succeeded: 9, failed: 1 }, daily: [{ day: '2026-10-13', visitors: 12, analyses: 12 }] } });
    if (path.endsWith('/records')) {
      listQueries.push(url.search);
      return route.fulfill({ json: { total: 1, page: 1, page_size: 50, items: [{ id: 'one', visitor: 'testvisitor1', at: new Date().toISOString(), state: url.searchParams.get('kind') === 'analyses' ? 'failed' : undefined, stage: 'opinion', started_at: '2026-10-13T00:00:00Z', finished_at: '2026-10-13T00:02:05Z', email: url.searchParams.get('kind') === 'reports' ? 'user@example.com' : undefined, failure: url.searchParams.get('kind') === 'analyses' ? 'SEARCH_RATE_LIMIT' : undefined }] } });
    }
    return route.fulfill({ json: { id: 'one', visitor: 'testvisitor1', state: 'failed', input: { holdings: [{ name: 'KODEX 200', instrument_id: 'KR:069500', account: '일반 계좌', amount_krw: '1000000' }], input_mode: 'amount', question: '<img src=x onerror=alert(1)>', horizon: '10년 이상', preferences: { note: '내 입력 메모', interests: ['반도체'], accounts: ['일반 계좌'], contribution: '매달 100만 원' } } } });
  });
  await page.goto('/portfolio/admin.html');
  await expect(page.getByRole('heading', { name: '지금, ORCA' })).toBeVisible();
  await expect(page.locator('.stat').first().locator('strong')).toHaveText('2');
  await expect(page.locator('.records')).toContainText('최종 의견 · 2분 5초');
  await page.getByRole('button', { name: 'testvisitor1 상세 보기' }).click();
  await expect(page.getByRole('region', { name: '입력 상세' })).toContainText('1,000,000');
  await expect(page.getByRole('region', { name: '입력 상세' })).toContainText('<img src=x onerror=alert(1)>');
  expect(await page.locator('.detail img').count()).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/admin-detail-${test.info().project.name}.png`, fullPage: true });
  await page.getByRole('button', { name: '상세 닫기', exact: true }).click();
  await page.getByRole('button', { name: '리포트 신청' }).click();
  await expect(page.getByText('user@example.com')).toBeVisible();
  await page.getByLabel('조회 기간').selectOption('30');
  await expect.poll(() => listQueries.some(q => q.includes('days=30'))).toBe(true);
  count = 3;
  await page.clock.install();
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await expect(page.locator('.stat').first().locator('strong')).toHaveText('3');
  count = 4;
  await page.clock.runFor(11000);
  await expect(page.locator('.stat').first().locator('strong')).toHaveText('4');
  await page.screenshot({ path: `test-results/admin-${test.info().project.name}.png`, fullPage: true });
  expired = true;
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await expect(page.getByRole('button', { name: '다시 연결' })).toBeVisible();
  await expect(page.getByText('user@example.com')).toHaveCount(0);
});

test('refresh failures remain visible and pagination does not mix records', async ({ page }) => {
  let failing = false;
  await page.route('**/api/portfolio/admin/**', async route => {
    const url = new URL(route.request().url());
    if (failing) return route.fulfill({ status: 503, json: {} });
    if (url.pathname.endsWith('/overview')) return route.fulfill({ json: { as_of: new Date().toISOString(), active_visitors: 1, today_visitors: 1, queued: 0, running: 0, today_jobs: { queued: 0, running: 0, succeeded: 0, failed: 0 }, daily: [] } });
    const current = Number(url.searchParams.get('page'));
    return route.fulfill({ json: { total: 51, page: current, page_size: 50, items: [{ id: String(current), visitor: `visitor-page-${current}`, at: new Date().toISOString(), state: 'queued' }] } });
  });
  await page.goto('/portfolio/admin.html');
  await expect(page.getByText('visitor-page-1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByText('visitor-page-2', { exact: true })).toBeVisible();
  await expect(page.getByText('visitor-page-1', { exact: true })).toHaveCount(0);
  failing = true;
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await expect(page.getByRole('heading', { name: '연결이 끊겼어요' })).toBeVisible();
  await expect(page.getByText('visitor-page-2', { exact: true })).toHaveCount(0);
  failing = false;
  await page.getByRole('button', { name: '다시 연결' }).click();
  await expect(page.getByText('visitor-page-2', { exact: true })).toBeVisible();
});

test('deleted selected drafts do not freeze live overview and records', async ({ page }) => {
  let deleted = false;
  await page.route('**/api/portfolio/admin/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/overview')) return route.fulfill({ json: { as_of: new Date().toISOString(), active_visitors: deleted ? 9 : 1, today_visitors: 1, queued: 0, running: 0, today_jobs: { queued: 0, running: 0, succeeded: 0, failed: 0 }, daily: [] } });
    if (path.endsWith('/records')) return route.fulfill({ json: { total: deleted ? 0 : 1, page: 1, page_size: 50, items: deleted ? [] : [{ id: 'old', visitor: 'draftvisitor', at: new Date().toISOString() }] } });
    return deleted ? route.fulfill({ status: 404, json: {} }) : route.fulfill({ json: { id: 'old', visitor: 'draftvisitor', input: { holdings: [], question: 'saved question' } } });
  });
  await page.goto('/portfolio/admin.html');
  await page.getByRole('button', { name: '저장된 입력' }).click();
  await page.getByRole('button', { name: 'draftvisitor 상세 보기' }).click();
  await expect(page.getByRole('region', { name: '입력 상세' })).toContainText('saved question');
  deleted = true;
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await expect(page.locator('.stat').first().locator('strong')).toHaveText('9');
  await expect(page.getByRole('region', { name: '입력 상세' })).toHaveCount(0);
  await expect(page.getByText('이 기간에는 기록이 없습니다.')).toBeVisible();
});

test('anonymous visitor heartbeat is separate from the admin page', async ({ page }) => {
  let pulses = 0;
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().endsWith('/presence')) pulses++;
    return route.fulfill({ json: { instruments: [] } });
  });
  await page.goto('/portfolio/');
  await expect.poll(() => pulses).toBeGreaterThan(0);
  await page.clock.install();
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const hiddenCount = pulses;
  await page.clock.runFor(60000);
  expect(pulses).toBe(hiddenCount);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => pulses).toBe(hiddenCount + 1);
  const before = pulses;
  await page.goto('/portfolio/admin.html');
  await page.waitForTimeout(500);
  expect(pulses).toBe(before);
});

test('a fast polling error clears private data despite a later failed response', async ({ page }) => {
  await page.clock.install();
  let expired = false;
  await page.route('**/api/portfolio/admin/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (expired) {
      if (path.endsWith('/overview')) return route.fulfill({ status: 503, json: {} });
      await new Promise(resolve => setTimeout(resolve, 150));
      return route.fulfill({ status: 401, json: {} });
    }
    if (path.endsWith('/overview')) return route.fulfill({ json: { as_of: new Date().toISOString(), active_visitors: 1, today_visitors: 1, queued: 0, running: 0, today_jobs: { queued: 0, running: 0, succeeded: 0, failed: 0 }, daily: [] } });
    return route.fulfill({ json: { total: 1, page: 1, page_size: 50, items: [{ id: 'private-id', visitor: 'privatevisitor', at: new Date().toISOString(), state: 'queued' }] } });
  });
  await page.goto('/portfolio/admin.html');
  await expect(page.getByText('privatevisitor', { exact: true })).toBeVisible();
  expired = true;
  await page.clock.runFor(10000);
  await expect(page.getByRole('button', { name: '다시 연결' })).toBeVisible();
  await expect(page.getByText('privatevisitor', { exact: true })).toHaveCount(0);
});

test('presence and first input save share one in-flight visitor session', async ({ page }) => {
  let sessionCalls = 0;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/portfolio/**', async route => {
    if (route.request().url().endsWith('/session')) {
      sessionCalls++;
      await gate;
    }
    return route.fulfill({ json: { instruments: [] } });
  });
  await page.goto('/portfolio/');
  await expect.poll(() => sessionCalls).toBe(1);
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.waitForTimeout(200);
  expect(sessionCalls).toBe(1);
  release();
  await expect(page.getByRole('button', { name: '매달 꾸준히', exact: true })).toBeVisible();
});

test('two first-visit tabs share the cookie before recording presence', async ({ context, page }) => {
  let sessions = 0;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const owners = new Set<string>();
  await context.route('**/api/portfolio/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const cookie = (await route.request().allHeaders()).cookie || '';
    if (path.endsWith('/session')) {
      sessions++;
      const owner = /edge_portfolio_visitor=([^;]+)/.exec(cookie)?.[1] || `visitor-${sessions}`;
      await gate;
      return route.fulfill({ headers: { 'Set-Cookie': `edge_portfolio_visitor=${owner}; Path=/; HttpOnly; SameSite=Lax` }, json: {} });
    }
    if (path.endsWith('/presence')) owners.add(cookie);
    return route.fulfill({ json: { instruments: [] } });
  });
  const second = await context.newPage();
  await page.goto('/portfolio/');
  await expect.poll(() => sessions).toBe(1);
  await second.goto('/portfolio/');
  await second.getByRole('button', { name: '다음', exact: true }).click();
  await second.waitForTimeout(200);
  expect(sessions).toBe(1);
  release();
  await expect.poll(() => sessions).toBe(2);
  await expect.poll(() => owners.size).toBe(1);
  await expect.poll(async () => (await context.cookies()).find(cookie => cookie.name === 'edge_portfolio_visitor')?.value).toBe('visitor-1');
  await second.close();
});
