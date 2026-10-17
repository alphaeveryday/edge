import { test, expect, type Page } from '@playwright/test';
import { gunzipSync } from 'node:zlib';

type Event = { event: string; properties: Record<string, any> };
test.use({ launchOptions: { args: ['--disable-blink-features=AutomationControlled'] }, userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36' });
async function instrument(page: Page, baseURL: string, blocked = false) {
  const events: Event[] = [];
  // Match a human Chromium client, including metadata retained during navigation.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setUserAgentOverride', {
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    userAgentMetadata: { brands: [{ brand: 'Google Chrome', version: '130' }], fullVersion: '130.0.0.0', platform: 'Windows', platformVersion: '10.0.0', architecture: 'x86', model: '', mobile: false },
  });
  await page.context().route('https://*.posthog.com/**', async route => {
    if (blocked) return route.abort();
    const request = route.request();
    const buffer = request.postDataBuffer();
    if (buffer && new URL(request.url()).pathname === '/e/') {
      const compression = new URL(request.url()).searchParams.get('compression');
      const raw = buffer[0] === 0x1f && buffer[1] === 0x8b ? gunzipSync(buffer).toString() : compression === 'base64' ? Buffer.from(new URLSearchParams(buffer.toString()).get('data')!, 'base64').toString() : buffer.toString();
      const data = JSON.parse(raw);
      events.push(...(Array.isArray(data) ? data : data.batch || [data]));
    }
    await route.fulfill({ json: {}, headers: { 'Access-Control-Allow-Origin': 'https://etforca.com', 'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'POST, GET, OPTIONS' } });
  });
  await page.route('https://etforca.com/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/api/')) return route.fulfill({ json: {} });
    const response = await route.fetch({ url: `${baseURL}${url.pathname}${url.search}` });
    await route.fulfill({ response });
  });
  return events;
}

test('each input screen has one visit and visible dwell excludes hidden time without collecting customer text', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.clock.install();
  await page.goto('https://etforca.com/portfolio/?email=private@example.com#secret');
  await expect(page.getByRole('heading', { name: '내 포트폴리오를 더 좋게 바꿔볼까요?' })).toBeVisible();
  await page.clock.runFor(4000);
  await expect.poll(() => events.filter(e => e.event === '$pageview').length).toBe(1);
  await page.clock.runFor(5000);
  await page.evaluate(() => {
    document.body.append(Object.assign(document.createElement('p'), { textContent: 'private-customer-holdings' }));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.clock.runFor(60000);
  const hiddenCount = events.filter(e => e.event === 'portfolio_screen_engagement').length;
  await page.clock.runFor(60000);
  expect(events.filter(e => e.event === 'portfolio_screen_engagement').length).toBe(hiddenCount);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.clock.runFor(3000);
  await page.getByRole('button', { name: '종목부터 추천받기' }).click();
  await page.clock.runFor(5000);
  await expect.poll(() => events.filter(e => e.event === '$pageview').map(e => e.properties.screen)).toEqual(['input_holdings', 'input_funding']);
  const dwell = events.filter(e => e.event === 'portfolio_screen_engagement' && e.properties.screen === 'input_holdings').reduce((sum, e) => sum + e.properties.visible_seconds, 0);
  expect(dwell).toBeGreaterThanOrEqual(8);
  expect(dwell).toBeLessThan(20);
  expect(JSON.stringify(events)).not.toMatch(/private@example|private-customer|#secret|email=/);
  expect(events.every(e => e.properties.distinct_id === '$posthog_cookieless')).toBe(true);
  // Without the mode flag, ingestion cannot replace the shared sentinel with anonymous visit IDs.
  for (const event of events) expect(event.properties).toMatchObject({
    $cookieless_mode: true, $host: 'etforca.com',
    $current_url: 'https://etforca.com/portfolio/', $pathname: '/portfolio/',
  });
  expect(await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('ph_')))).toEqual([]);
});

test('real navigation hands final scroll, dwell and leave events to browser beacons', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'private-analysis-id'));
  await page.route('**/api/portfolio/analyses/private-analysis-id', route => route.fulfill({ json: {
    id: 'private-analysis-id', state: 'succeeded', result: { sections: { opinion: { paragraphs: Array(20).fill('private-result-text') } } },
  } }));
  await page.goto('https://etforca.com/portfolio/');
  await expect(page.locator('[data-analytics-section="conclusion"]')).toBeVisible();
  await expect.poll(() => events.some(e => e.properties.screen === 'result')).toBe(true);
  // Chromium restores automation metadata during navigation; isolate delivery from bot detection.
  await page.evaluate(async () => {
    const url = performance.getEntriesByType('resource').find(entry => entry.name.includes('/posthog-js.js'))!.name;
    const sdk = await import(/* @vite-ignore */ url);
    sdk.default.set_config({ opt_out_useragent_filter: true });
    sdk.default.on('eventCaptured', (event: unknown) => {
      const events = JSON.parse(sessionStorage.getItem('exit-events') || '[]');
      events.push(event); sessionStorage.setItem('exit-events', JSON.stringify(events));
    });
    const send = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = (url, data) => {
      const accepted = send(url, data);
      if (accepted && String(url).startsWith('https://us.i.posthog.com/e/')) sessionStorage.setItem('exit-beacons', String(Number(sessionStorage.getItem('exit-beacons') || 0) + 1));
      return accepted;
    };
  });
  await page.locator('[data-analytics-section="conclusion"]').evaluate(el => el.scrollIntoView());
  await page.waitForTimeout(3500);
  // A real navigation gives the departing document no extra timer ticks to flush.
  await page.goto('https://etforca.com/end');
  const terminal = await page.evaluate(() => ({ events: JSON.parse(sessionStorage.getItem('exit-events') || '[]'), beacons: Number(sessionStorage.getItem('exit-beacons') || 0) }));
  expect(terminal.events.some((e: Event) => e.event === 'portfolio_scroll_pause' && e.properties.section === 'conclusion' && e.properties.pause_seconds >= 3)).toBe(true);
  expect(terminal.events.some((e: Event) => e.event === '$pageleave')).toBe(true);
  expect(terminal.beacons).toBeGreaterThanOrEqual(3);
  expect(JSON.stringify(terminal.events)).not.toMatch(/private-analysis-id|private-result-text/);
  const count = events.length;
  await page.clock.install();
  await page.clock.runFor(30000);
  expect(events.length).toBe(count);
  expect(JSON.stringify(events)).not.toMatch(/private-analysis-id|private-result-text/);
});

test('PostHog outage does not prevent entering the next input screen', async ({ page, baseURL }) => {
  await instrument(page, baseURL!, true);
  await page.goto('https://etforca.com/portfolio/');
  await page.getByRole('button', { name: '종목부터 추천받기' }).click();
  await expect(page.locator('.nav__n')).toHaveText('2 / 5');
});

test('expanding content updates the visible section even without scrolling', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.goto('https://etforca.com/portfolio/');
  await expect.poll(() => events.some(e => e.event === '$pageview')).toBe(true);
  await page.evaluate(() => {
    document.getElementById('root')!.innerHTML = '<details data-analytics-section="recommendation"><summary>추가 설명</summary><div style="height:1500px"></div></details><section data-analytics-section="conclusion" style="height:1500px">결론</section>';
  });
  await page.waitForTimeout(2500);
  await page.getByText('추가 설명').click();
  await page.waitForTimeout(2500);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => events.filter(e => e.event === 'portfolio_scroll_pause').map(e => e.properties.section)).toEqual(expect.arrayContaining(['conclusion', 'recommendation']));
});

test('a result loaded in a background tab is not a view until the customer sees it', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.addInitScript(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    localStorage.setItem('edge-portfolio-job', 'background');
  });
  await page.route('**/api/portfolio/analyses/background', route => route.fulfill({ json: { id: 'background', state: 'succeeded', result: { sections: {} } } }));
  await page.goto('https://etforca.com/portfolio/');
  await expect(page.locator('#report')).toBeAttached();
  await page.waitForTimeout(3500);
  expect(events).toEqual([]);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => events.filter(e => e.event === '$pageview').map(e => e.properties.screen)).toEqual(['result']);
});

test('local previews and administrator pages do not contact PostHog', async ({ page }) => {
  const calls: string[] = [];
  page.on('request', req => { if (new URL(req.url()).hostname.endsWith('posthog.com')) calls.push(req.url()); });
  await page.route('**/api/portfolio/**', route => route.fulfill({ json: {} }));
  await page.goto('/portfolio/');
  await page.waitForTimeout(500);
  await page.goto('/portfolio/admin.html');
  await page.waitForTimeout(500);
  expect(calls).toEqual([]);
});

async function feedbackReport(page: Page) {
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'feedback-private-id'));
  await page.route('**/api/portfolio/analyses/feedback-private-id', route => route.fulfill({ json: {
    id: 'feedback-private-id', state: 'succeeded', result: {
      recommendation: { title: 'private recommendation', allocations: [], actions: [], unavailable_reason: '성과 자료 없음' },
      sections: { opinion: { paragraphs: Array(12).fill('private result text') } },
    },
  } }));
  await page.goto('https://etforca.com/portfolio/');
  await expect(page.locator('.performance')).toBeAttached();
}

for (const [label, rating] of [['좋아요', 'good'], ['그냥 그래요', 'okay'], ['개선이 필요해요', 'needs_improvement']]) {
  test(`feedback after performance submits ${rating} once without customer data`, async ({ page, baseURL }) => {
    const events = await instrument(page, baseURL!);
    await feedbackReport(page);
    const popup = page.getByRole('dialog', { name: '포트폴리오 저장' });
    await expect(popup).toHaveCount(0);
    await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
    await expect(popup).toBeVisible();
    await expect(popup).toBeFocused();
    if (rating === 'good') await popup.screenshot({ path: `test-results/feedback-${test.info().project.name}.png` });
    const bounds = await popup.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
    await popup.getByRole('button', { name: label, exact: true }).click();
    await expect(popup.getByRole('status')).toHaveText('의견 감사합니다!');
    await expect(popup).toBeFocused();
    await expect.poll(() => events.filter(e => e.event === 'portfolio_feedback').map(e => e.properties.action)).toEqual(['shown', 'submitted']);
    const response = events.find(e => e.properties.action === 'submitted')!;
    expect(response.properties).toMatchObject({ rating, screen: 'result', section: 'feedback', $cookieless_mode: true, $host: 'etforca.com' });
    expect(JSON.stringify(events)).not.toMatch(/feedback-private-id|private recommendation|private result text/);
    await popup.getByRole('button', { name: '평가 팝업 닫기' }).click();
    await page.reload();
    await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
    await expect(popup).toHaveCount(0);
    expect(events.filter(e => e.event === 'portfolio_feedback')).toHaveLength(2);
  });
}

test('background report waits until visible and Escape dismisses without a rating', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.addInitScript(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }));
  await feedbackReport(page);
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  await expect(page.locator('.report-feedback')).toHaveCount(0);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.locator('.report-feedback')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.report-feedback')).toHaveCount(0);
  await expect.poll(() => events.filter(e => e.event === 'portfolio_feedback').map(e => e.properties.action)).toEqual(['shown', 'dismissed']);
  expect(events.filter(e => e.event === 'portfolio_feedback').every(e => e.properties.rating === undefined)).toBe(true);
  await page.reload();
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page.locator('.report-feedback')).toHaveCount(0);
});

test('closing the premium modal does not dismiss unanswered feedback', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await feedbackReport(page);
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  await expect(page.locator('.report-feedback')).toBeVisible();
  const premium = page.getByRole('button', { name: '프리미엄 투자 전략 설계 리포트 받기' });
  await premium.focus();
  await premium.press('Enter');
  await expect(page.getByRole('dialog', { name: '프리미엄 투자 전략 리포트' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '프리미엄 투자 전략 리포트' })).not.toBeVisible();
  await expect(page.locator('.report-feedback')).toBeVisible();
  await page.getByRole('button', { name: '좋아요', exact: true }).click();
  await expect.poll(() => events.filter(e => e.event === 'portfolio_feedback').map(e => e.properties.action)).toEqual(['shown', 'submitted']);
});

test('feedback remains usable when PostHog and feedback storage are unavailable', async ({ page, baseURL }) => {
  await instrument(page, baseURL!, true);
  await page.addInitScript(() => {
    const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
    Storage.prototype.getItem = function(key) { if (key.startsWith('edge-portfolio-feedback:')) throw new Error('blocked'); return get.call(this, key); };
    Storage.prototype.setItem = function(key, value) { if (key.startsWith('edge-portfolio-feedback:')) throw new Error('blocked'); return set.call(this, key, value); };
  });
  await feedbackReport(page);
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  await page.getByRole('button', { name: '좋아요', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('의견 감사합니다!');
  await page.getByRole('button', { name: '평가 팝업 닫기' }).click();
  await expect(page.locator('.report-feedback')).toHaveCount(0);
  await page.getByRole('button', { name: '새 포트폴리오 분석하기' }).click();
  await expect(page.getByRole('heading', { name: '내 포트폴리오를 더 좋게 바꿔볼까요?' })).toBeVisible();
});

test('feedback stays reachable in a short viewport at high zoom', async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 320, height: 150 });
  await instrument(page, baseURL!);
  await feedbackReport(page);
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  const popup = page.locator('.report-feedback');
  await expect(popup).toBeVisible();
  const bounds = await popup.boundingBox();
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(150);
  await popup.getByRole('button', { name: /./ }).last().click();
  await expect(popup.getByRole('status')).toBeVisible();
  await popup.getByRole('button', { name: '평가 팝업 닫기' }).click();
  await expect(popup).toHaveCount(0);
});

test('newsletter signup is above the independent rating choices and never enters analytics', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  const submissions: unknown[] = [];
  await page.route('**/api/portfolio/analyses/*/newsletter', route => {
    submissions.push(route.request().postDataJSON());
    return route.fulfill({ json: { status: 'pending', delivery_ready: true } });
  });
  await feedbackReport(page);
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  const popup = page.locator('.report-feedback');
  const form = await popup.locator('form').boundingBox();
  const choices = await popup.locator('.feedback-choices').boundingBox();
  expect(form!.y + form!.height).toBeLessThan(choices!.y);
  await popup.getByLabel('뉴스레터 이메일').fill('newsletter-private@example.test');
  await popup.getByRole('button', { name: '매일 핵심 소식 받기' }).click();
  await expect(popup.getByRole('checkbox')).toHaveCount(0);
  await expect(popup.getByRole('status')).toContainText('확인 메일');
  expect(submissions).toEqual([{ email: 'newsletter-private@example.test', consent: true }]);
  await popup.getByRole('button', { name: '좋아요', exact: true }).click();
  await expect.poll(() => events.filter(e => e.event === 'portfolio_feedback').map(e => e.properties.action)).toEqual(['shown', 'submitted']);
  expect(JSON.stringify(events)).not.toMatch(/newsletter-private|example.test/);
});

test('newsletter confirmation requires a click and clears the token before any analytics', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  const analyticsRequests: string[] = [];
  page.on('request', request => { if (new URL(request.url()).hostname.endsWith('posthog.com')) analyticsRequests.push(request.url()); });
  const token = 'private-newsletter-confirmation-token-123456789';
  const confirmations: unknown[] = [];
  await page.route('**/api/portfolio/newsletter/confirm', route => {
    confirmations.push(route.request().postDataJSON());
    return route.fulfill({ json: { status: 'confirmed', delivery_ready: false } });
  });
  await page.goto(`https://etforca.com/portfolio/#newsletter-confirm=${token}`);
  await expect(page.getByRole('heading', { name: '매일 내 ETF의 핵심 소식을 받아보세요' })).toBeVisible();
  expect(page.url()).toBe('https://etforca.com/portfolio/');
  expect(confirmations).toHaveLength(0);
  await page.getByRole('button', { name: '이메일 확인하고 구독하기' }).click();
  await expect(page.getByRole('heading', { name: '이메일 확인을 마쳤어요!' })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('발송 준비가 끝나면');
  expect(confirmations).toEqual([{ token }]);
  expect(events).toHaveLength(0);
  expect(analyticsRequests).toHaveLength(0);
});

test('newsletter confirmation links work in an existing tab and each token still requires a click', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  const confirmations: unknown[] = [];
  await page.route('**/api/portfolio/newsletter/confirm', route => {
    confirmations.push(route.request().postDataJSON());
    return route.fulfill({ json: { status: 'confirmed', delivery_ready: false } });
  });
  await page.goto('https://etforca.com/portfolio/');
  await expect(page.getByRole('heading', { name: '내 포트폴리오를 더 좋게 바꿔볼까요?' })).toBeVisible();
  for (const [index, token] of ['private-first-confirmation-token', 'private-second-confirmation-token'].entries()) {
    await page.evaluate(value => { location.hash = 'newsletter-confirm=' + value; }, token);
    await expect(page.getByRole('heading', { name: '매일 내 ETF의 핵심 소식을 받아보세요' })).toBeVisible();
    expect(page.url()).toBe('https://etforca.com/portfolio/');
    expect(confirmations).toHaveLength(index);
    await page.getByRole('button', { name: '이메일 확인하고 구독하기' }).click();
    await expect(page.getByRole('heading', { name: '이메일 확인을 마쳤어요!' })).toBeVisible();
    expect(confirmations[index]).toEqual({ token });
  }
  expect(JSON.stringify(events)).not.toMatch(/private-first|private-second|newsletter-confirm/);
});

test('dismissed feedback keeps a newsletter retry without asking for another rating', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await feedbackReport(page);
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  await page.getByRole('button', { name: '평가 팝업 닫기' }).click();
  await page.reload();
  const launcher = page.getByRole('button', { name: '보유 종목 뉴스레터 받기' });
  const launcherBox = await launcher.boundingBox();
  const evaluationBox = await page.locator('.evaluation').boundingBox();
  expect(launcherBox!.y + launcherBox!.height).toBeLessThanOrEqual(evaluationBox!.y);
  await launcher.click();
  const popup = page.getByRole('dialog', { name: '내 ETF 소식, 매일 메일로' });
  await expect(popup.getByLabel('뉴스레터 이메일')).toBeVisible();
  await expect(popup.locator('.feedback-choices')).toHaveCount(0);
  await page.route('**/api/portfolio/analyses/*/newsletter', route => route.fulfill({ json: { status: 'active', delivery_ready: false } }));
  await popup.getByLabel('뉴스레터 이메일').fill('reader@example.test');
  await popup.getByRole('button', { name: '매일 핵심 소식 받기' }).click();
  await expect(popup.getByRole('status')).toContainText('발송 준비가 끝나면');
  await popup.getByRole('button', { name: '평가 팝업 닫기' }).click();
  await expect(launcher).toBeFocused();
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  await expect(popup).toHaveCount(0);
  await expect.poll(() => events.filter(e => e.event === 'portfolio_feedback').map(e => e.properties.action)).toEqual(['shown', 'dismissed']);
});

test('newsletter validation explains the email field rather than portfolio holdings', async ({ page, baseURL }) => {
  await instrument(page, baseURL!);
  await feedbackReport(page);
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  await page.route('**/api/portfolio/analyses/*/newsletter', route => route.fulfill({ status: 422, json: { detail: [{ loc: ['body', 'email'], type: 'string_pattern_mismatch' }] } }));
  const popup = page.locator('.report-feedback');
  await popup.getByLabel('뉴스레터 이메일').fill('reader@example');
  await popup.getByRole('button', { name: '매일 핵심 소식 받기' }).click();
  await expect(popup.getByRole('alert')).toHaveText('이메일 주소를 확인해 주세요.');
});

test('an already open newsletter launcher preserves feedback mode and Escape accounting', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await feedbackReport(page);
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  const launcher = page.getByRole('button', { name: '보유 종목 뉴스레터 받기' });
  await launcher.focus();
  await launcher.press('Enter');
  await expect(page.locator('.feedback-choices')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.report-feedback')).toHaveCount(0);
  await expect.poll(() => events.filter(e => e.event === 'portfolio_feedback').map(e => e.properties.action)).toEqual(['shown', 'dismissed']);
});

for (const alreadyShown of [false, true]) test(`newsletter never covers an active followup form (already shown: ${alreadyShown})`, async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await feedbackReport(page);
  if (alreadyShown) {
    await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
    await expect(page.locator('.report-feedback')).toBeVisible();
  }
  const question = page.getByLabel('추가 질문', { exact: true });
  await question.fill('이 추천에서 더 궁금한 내용이 있어요');
  await expect(page.locator('.report-feedback')).toHaveCount(0);
  await expect(question).toBeFocused();
  await question.blur();
  await page.locator('.evaluation').evaluate(el => el.scrollIntoView());
  await expect(page.locator('.report-feedback')).toHaveCount(0);
  await expect.poll(() => events.filter(e => e.event === 'portfolio_feedback').map(e => e.properties.action)).toEqual(alreadyShown ? ['shown', 'dismissed'] : []);
});
