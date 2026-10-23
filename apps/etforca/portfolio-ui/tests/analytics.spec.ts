import { test, expect, type Page } from '@playwright/test';
import { gunzipSync } from 'node:zlib';
import { readFileSync } from 'node:fs';
import { blockAnalyticsHost } from '../playwright.config';

type Event = { event: string; properties: Record<string, any> };
function unpackReplay(value: any): any {
  if (typeof value === 'string' && value.charCodeAt(0) === 31 && value.charCodeAt(1) === 139) return unpackReplay(JSON.parse(gunzipSync(Buffer.from(value, 'latin1')).toString()));
  if (Array.isArray(value)) return value.map(unpackReplay);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, unpackReplay(v)]));
  return value;
}
test.use({ launchOptions: { args: ['--disable-blink-features=AutomationControlled', blockAnalyticsHost] }, userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36' });
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'wait' }); });

test('confirmed test visitors retain events with an explicit customer-exclusion marker', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.route('**/api/portfolio/session', route => route.fulfill({ json: { is_test: true } }));
  await page.goto('https://etforca.com/portfolio/');
  await expect.poll(() => events.some(event => event.event === '$pageview' && event.properties.is_test === true)).toBe(true);
  expect(events.filter(event => event.event === '$pageview').every(event => event.properties.is_test === true)).toBe(true);
});

test('screen transitions refresh mixed visitors instead of keeping their prior test-only classification', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  let isTest = true;
  await page.route('**/api/portfolio/session', route => route.fulfill({ json: { is_test: isTest } }));
  await page.goto('https://etforca.com/portfolio/');
  await expect.poll(() => events.some(e => e.event === '$pageview' && e.properties.is_test === true)).toBe(true);
  isTest = false;
  await page.getByRole('button', { name: '종목부터 추천받기' }).click();
  await expect.poll(() => events.some(e => e.event === '$pageview' && e.properties.screen === 'input_funding' && e.properties.is_test === false)).toBe(true);
});

test('unknown classification withholds analytics without blocking input and recovers next screen', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  let classification: boolean | null = null;
  await page.route('**/api/portfolio/session', route => route.fulfill({ json: { is_test: classification } }));
  await page.goto('https://etforca.com/portfolio/');
  await page.getByRole('button', { name: '종목부터 추천받기' }).click();
  await expect(page.getByRole('button', { name: '이미 다 매수한 상태에요', exact: true })).toBeVisible();
  expect(events).toEqual([]);
  classification = false;
  await page.getByRole('button', { name: '이미 다 매수한 상태에요', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect.poll(() => events.some(e => e.event === '$pageview' && e.properties.is_test === false)).toBe(true);
});

test('session replay shows screen content while excluding auth cookies and respecting opt out', async ({ page, baseURL }) => {
  test.setTimeout(60000);
  const events = await instrument(page, baseURL!, false, true);
  await page.addInitScript(() => {
    document.cookie = 'auth-secret=never-record-this;path=/;Secure';
    document.addEventListener('DOMContentLoaded', () => {
      const tax = document.createElement('div'); tax.className = 'tax-dialog'; tax.innerHTML = '<input type="checkbox" class="private-tax-check"><input type="radio" class="private-tax-radio">'; document.body.append(tax);
    });
  });
  await page.goto('https://etforca.com/portfolio/?email=private@example.com#private-token');
  await expect.poll(() => events.some(e => e.event === '$pageview')).toBe(true);
  await page.getByRole('button', { name: '종목부터 추천받기' }).click();
  await expect.poll(() => events.some(e => e.event === '$snapshot'), { timeout: 20000 }).toBe(true);
  const mutationTime = await page.evaluate(() => {
    const p = document.createElement('p'); p.textContent = 'private-holdings-value-123456789'; p.title = 'private-title-value'; document.body.append(p);
    const input = document.createElement('input'); input.value = 'private-input-value'; document.body.append(input); input.dispatchEvent(new Event('input', { bubbles: true }));
    const chart = document.createElement('div'); chart.className = 'dividend-plot'; chart.innerHTML = '<i style="height:37.1234567%">private-financial-chart</i>'; document.body.append(chart);
    const gain = document.createElement('span'); gain.className = 'gain'; gain.textContent = 'private-profit'; document.body.append(gain);
    const choices = document.createElement('div'); choices.className = 'chips big'; choices.innerHTML = '<button class="private-choice-marker" aria-pressed="true">private-answer</button>'; document.querySelector('.step')!.append(choices);
    document.querySelector<HTMLInputElement>('.private-tax-check')!.click();
    document.querySelector<HTMLInputElement>('.private-tax-radio')!.click();
    return Date.now();
  });
  await expect.poll(() => events.filter(e => e.event === '$snapshot').flatMap(e => unpackReplay(e.properties.$snapshot_data)).some(e => e.type === 3 && e.timestamp >= mutationTime && e.data.source === 0), { timeout: 20000 }).toBe(true);
  const serialized = JSON.stringify(unpackReplay(events));
  expect(serialized).not.toMatch(/private@example|private-token|never-record-this|auth-secret/);
  expect(serialized).toContain('.nav__pill');
  for (const value of ['private-holdings-value-123456789', 'private-input-value', 'private-title-value', 'private-financial-chart', 'private-profit', 'private-answer']) expect(serialized).toContain(value);
  const id = events.find(e => e.event === '$pageview')!.properties.distinct_id;
  expect(events.filter(e => e.event === '$snapshot').every(e => e.properties.$session_id && e.properties.distinct_id === id)).toBe(true);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByText('이용 통계 설정', { exact: true })).toBeVisible();
  await expect.poll(async () => {
    const cookie = (await page.context().cookies()).find(c => c.name === 'ph_orca_portfolio');
    return cookie ? JSON.parse(decodeURIComponent(cookie.value)).distinct_id : null;
  }).toBe(id);
  await page.getByText('이용 통계 설정', { exact: true }).click();
  await page.getByRole('button', { name: '이용 통계 끄기' }).click();
  await page.waitForTimeout(3000);
  const count = events.length;
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(4000);
  expect(events.length).toBe(count);
  expect((await page.context().cookies()).filter(c => c.name.startsWith('ph_'))).toEqual([]);
  await page.getByText('이용 통계 설정', { exact: true }).click();
  await page.getByRole('button', { name: '이용 통계 켜기' }).click();
  await expect.poll(async () => (await page.context().cookies()).some(c => c.name === 'ph_orca_portfolio')).toBe(true);
  expect(await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('__ph_opt_in_out_')).map(k => localStorage.getItem(k)))).toContain('1');
});
async function instrument(page: Page, baseURL: string, blocked = false, replay = false) {
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
    if (replay && request.url().includes('recorder') && request.url().includes('.js')) return route.fulfill({ body: readFileSync('node_modules/posthog-js/dist/posthog-recorder.js'), contentType: 'application/javascript' });
    if (replay && request.url().includes('/config')) {
      const config = { sessionRecording: { endpoint: '/s/', recorderVersion: 'v2', sampleRate: 1, scriptConfig: { script: 'posthog-recorder' }, consoleLogRecordingEnabled: true } };
      if (request.resourceType() === 'script') return route.fulfill({ body: `window._POSTHOG_REMOTE_CONFIG = { phc_yiMfdC2Hi7iPBWHg7F9n3dBokvnXkjcsqFzN8Bs496R3: { config: ${JSON.stringify(config)} } };`, contentType: 'application/javascript' });
      return route.fulfill({ json: config });
    }
    const buffer = request.postDataBuffer();
    if (buffer && ['/e/', '/s/'].includes(new URL(request.url()).pathname)) {
      const compression = new URL(request.url()).searchParams.get('compression');
      const raw = buffer[0] === 0x1f && buffer[1] === 0x8b ? gunzipSync(buffer).toString() : compression === 'base64' ? Buffer.from(new URLSearchParams(buffer.toString()).get('data')!, 'base64').toString() : buffer.toString();
      const data = JSON.parse(raw);
      events.push(...(Array.isArray(data) ? data : data.batch || [data]));
    }
    await route.fulfill({ json: {}, headers: { 'Access-Control-Allow-Origin': 'https://etforca.com', 'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Headers': 'content-type', 'Access-Control-Allow-Methods': 'POST, GET, OPTIONS' } });
  });
  await page.route('https://etforca.com/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/api/')) return route.fulfill({ json: url.pathname.endsWith('/session') ? { is_test: false } : {} });
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
  expect(events.every(e => typeof e.properties.distinct_id === 'string' && e.properties.distinct_id !== '$posthog_cookieless')).toBe(true);
  expect((await page.context().cookies()).some(c => c.name.startsWith('ph_'))).toBe(true);
  // Anonymous cookie identity connects repeat visits without customer identity.
  // 화면 이벤트는 화면별 주소로, 화면이 없는 행동 이벤트(예: 추천 시작)는 포폴 기본 주소로 남는다 — 실제 URL·쿼리·해시는 남지 않는다.
  for (const event of events) {
    const path = event.properties.screen ? '/portfolio/' + String(event.properties.screen).replaceAll('_', '/') : '/portfolio/';
    expect(event.properties).toMatchObject({ $host: 'etforca.com', $current_url: 'https://etforca.com' + path, $pathname: path });
  }
  expect(await page.evaluate(() => Object.keys(localStorage).some(k => k.startsWith('ph_')))).toBe(true);
});

// 채널별 퍼널의 근거다 — 꼬리표가 빠지면 커뮤니티 유입이 direct 로 섞이고, 이전 채널 값이 남으면 다른 채널의 성과로 잘못 잡힌다.
test('entry campaign tags follow the visitor and a new channel replaces all of them', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  const pageviews = () => events.filter(e => e.event === '$pageview');
  await page.goto('https://etforca.com/portfolio/?utm_source=fmkorea&utm_medium=post&utm_campaign=portfolio_check&utm_content=1022');
  await expect.poll(() => pageviews().length).toBe(1);
  expect(pageviews()[0].properties).toMatchObject({ utm_source: 'fmkorea', utm_medium: 'post', utm_campaign: 'portfolio_check', utm_content: '1022' });
  await page.goto('https://etforca.com/portfolio/');
  await expect.poll(() => pageviews().length).toBe(2);
  expect(pageviews()[1].properties).toMatchObject({ utm_source: 'fmkorea', utm_content: '1022' });
  await page.goto(`https://etforca.com/portfolio/?utm_source=threads&utm_campaign=${'x'.repeat(150)}`);
  await expect.poll(() => pageviews().length).toBe(3);
  expect(pageviews()[2].properties).toMatchObject({ utm_source: 'threads', utm_campaign: 'x'.repeat(100) });
  expect(pageviews()[2].properties).not.toHaveProperty('utm_medium');
  expect(pageviews()[2].properties).not.toHaveProperty('utm_content');
});

test('real navigation hands final scroll, dwell and leave events to browser beacons', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'private-analysis-id'));
  await page.route('**/api/portfolio/analyses/private-analysis-id', route => route.fulfill({ json: {
    id: 'private-analysis-id', state: 'succeeded', result: { sections: { opinion: { paragraphs: Array(20).fill('private-result-text') } } },
  } }));
  await page.goto('https://etforca.com/portfolio/');
  await expect(page.locator('[data-analytics-section="analysis"]')).toBeVisible();
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
  await page.locator('[data-analytics-section="analysis"]').evaluate(el => el.scrollIntoView());
  await page.waitForTimeout(3500);
  // A real navigation gives the departing document no extra timer ticks to flush.
  await page.goto('https://etforca.com/end');
  const terminal = await page.evaluate(() => ({ events: JSON.parse(sessionStorage.getItem('exit-events') || '[]'), beacons: Number(sessionStorage.getItem('exit-beacons') || 0) }));
  expect(terminal.events.some((e: Event) => e.event === 'portfolio_scroll_pause' && e.properties.section === 'analysis' && e.properties.pause_seconds >= 3)).toBe(true);
  expect(terminal.events.some((e: Event) => e.event === '$pageleave')).toBe(true);
  expect(terminal.beacons).toBeGreaterThanOrEqual(3);
  // 분석 ID 는 결과 이벤트의 analysis_id 로만 간다(계획 '측정'). 결과 글은 어디에도 가지 않는다.
  expect(JSON.stringify(terminal.events)).not.toMatch(/private-result-text/);
  expect(terminal.events.filter((e: Event) => JSON.stringify(e).includes('private-analysis-id')).every((e: Event) => e.properties.analysis_id === 'private-analysis-id')).toBe(true);
  const count = events.length;
  await page.clock.install();
  await page.clock.runFor(30000);
  expect(events.length).toBe(count);
  expect(JSON.stringify(events)).not.toMatch(/private-result-text/);
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
  await expect(page.locator('#result')).toBeAttached();
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

// 8차 결과 측정(계획 '측정' 절). 옛 만족도 팝업(28번 노출에 응답 0건)은 결과 끝 설문 카드로 바뀌었다.
const resultEvents = ['result_shown', 'survey_shown', 'survey_answered', 'evidence_opened', 'ask_suggested_clicked', 'app_alert_clicked', 'store_link_clicked'];
async function resultReport(page: Page, id = 'result-analysis-id') {
  await page.addInitScript(value => localStorage.setItem('edge-portfolio-job', value), id);
  await page.route(`**/api/portfolio/analyses/${id}`, route => route.fulfill({ json: {
    id, state: 'succeeded', input: { question: 'private question text', horizon: '', holdings: [] }, result: {
      as_of: '2026-10-23T00:00:00Z', sources: [{ id: 's1', url: 'https://example.com/a' }],
      current_evaluation: { overall: 3, scores: { leadership: 3, profitability: 3, stability: 3 }, reason: { paragraphs: ['private result text'], evidence: [{ source_id: 's1', quote: 'private quote' }], limitations: [] } },
      metrics: { total_krw: '1000000', holdings: [], unavailable_reason: '' },
      recommendation: { title: 'private recommendation', total_krw: '1000000', unavailable_reason: '', actions: [], historical_performance: null,
        evaluation: { overall: 4, scores: null, reason: { paragraphs: ['추천 이유'], evidence: [], limitations: [] } },
        allocations: [{ instrument_id: 'KR:360750', name: 'TIGER 미국S&P500', ticker: '360750', kind: 'etf', account: 'ISA', weight_pct: 100, amount_krw: '1000000' }],
        execution: { status: 'ready', reasons: [], assumptions: [], additional_krw: '0', monthly_krw: '300000', required_additional_krw: null, months: null, accounts: [],
          rows: [{ instrument_id: 'KR:360750', name: 'TIGER 미국S&P500', account: 'ISA', before_krw: '1000000', target_krw: '1000000', buy_krw: '0', sell_krw: '0', cash_used_krw: '0' }],
          monthly_plan: [{ account: 'ISA', instrument_id: 'KR:360750', krw: '300000' }] } },
      suggested_questions: ['private suggested question', 'b', 'c', 'd'],
      sections: {},
    },
  } }));
  await page.route('**/api/portfolio/analyses/*/chats', route => route.fulfill({ json: { limit: 5, items: [] } }));
  await page.goto('https://etforca.com/portfolio/');
  await expect(page.locator('.r8-hero')).toBeVisible();
}

test('result events carry only the analysis id and survey codes, once each, and never customer text', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.route('**/api/portfolio/analyses/*/chat', route => route.fulfill({ status: 202, json: { id: 'c1', state: 'queued' } }));
  await resultReport(page);
  const result = () => events.filter(e => resultEvents.includes(e.event));
  await expect.poll(() => result().map(e => e.event)).toEqual(['result_shown']);
  // 설문은 카드가 보일 때 분석 하나에 한 번 보인 것으로 센다.
  await page.locator('[data-analytics-section=survey]').scrollIntoViewIfNeeded();
  await expect.poll(() => result().map(e => e.event)).toContain('survey_shown');
  await page.getByRole('button', { name: '충분히 반영했어요' }).click();
  await page.getByRole('button', { name: '조금 줄었어요' }).click();
  await page.getByText('근거 1개').click();
  await page.getByRole('button', { name: /매달 넣는 날 알림 받기/ }).click();
  const store = page.getByRole('link', { name: /App Store에서 무료로 받기/ });
  await store.evaluate(link => link.addEventListener('click', event => event.preventDefault()));
  await store.click();
  await page.getByRole('button', { name: 'private suggested question' }).click();
  await expect.poll(() => result().map(e => e.event).sort()).toEqual(['app_alert_clicked', 'ask_suggested_clicked', 'evidence_opened', 'result_shown', 'store_link_clicked', 'survey_answered', 'survey_answered', 'survey_shown']);
  expect(result().every(e => e.properties.analysis_id === 'result-analysis-id' && e.properties.screen === 'result')).toBe(true);
  expect(result().filter(e => e.event === 'survey_answered').map(e => [e.properties.question, e.properties.answer])).toEqual([['fit', 'enough'], ['worry', 'less']]);
  // 결과 글·질문·추천 이름은 이벤트로 보내지 않는다(화면 녹화는 별도 설정을 따른다).
  expect(JSON.stringify(events.filter(e => e.event !== '$snapshot'))).not.toMatch(/private (question|result|quote|recommendation|suggested)/);
  // 새로 고쳐도 같은 분석의 결과 노출·설문은 다시 세지 않는다.
  await page.reload();
  await expect(page.locator('.r8-hero')).toBeVisible();
  await expect(page.locator('[data-analytics-section=survey]')).toHaveCount(0);
  await page.waitForTimeout(1500);
  expect(events.filter(e => e.event === 'result_shown')).toHaveLength(1);
});

test('an empty result is not counted as shown', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.addInitScript(() => localStorage.setItem('edge-portfolio-job', 'empty'));
  await page.route('**/api/portfolio/analyses/empty', route => route.fulfill({ json: { id: 'empty', state: 'succeeded', result: { sections: {} } } }));
  await page.goto('https://etforca.com/portfolio/');
  await expect.poll(() => events.some(e => e.event === '$pageview')).toBe(true);
  await page.waitForTimeout(1500);
  expect(events.filter(e => e.event === 'result_shown')).toEqual([]);
  await expect(page.locator('[data-analytics-section=survey]')).toHaveCount(0);
});

test('the survey still works when PostHog and browser storage are unavailable', async ({ page, baseURL }) => {
  await instrument(page, baseURL!, true);
  await page.addInitScript(() => {
    const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
    Storage.prototype.getItem = function(key) { if (key.startsWith('edge-portfolio-survey:')) throw new Error('blocked'); return get.call(this, key); };
    Storage.prototype.setItem = function(key, value) { if (key.startsWith('edge-portfolio-survey:')) throw new Error('blocked'); return set.call(this, key, value); };
  });
  await resultReport(page);
  await page.getByRole('button', { name: '어느 정도 반영했어요' }).click();
  await page.getByRole('button', { name: '늘었어요' }).click();
  await page.getByRole('button', { name: '건너뛰기' }).click();
  await expect(page.getByRole('status').filter({ hasText: '답해 주셔서 고마워요' })).toBeVisible();
  await page.getByRole('button', { name: '새 포트폴리오 분석하기' }).click();
  await expect(page.getByRole('heading', { name: '내 포트폴리오를 더 좋게 바꿔볼까요?' })).toBeVisible();
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

test('input funnel steps are counted once per visit without the query or the amount', async ({ page, baseURL }) => {
  // 보고서의 핵심 지표(입력 화면 → 첫 검색 → 첫 종목 추가)를 재려면 이 이벤트가 있어야 하고, 고객 값은 남지 않아야 한다.
  const events = await instrument(page, baseURL!);
  await page.goto('https://etforca.com/portfolio/');
  const search = page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' });
  await search.fill('zzqprivate');
  await search.blur();
  await search.fill('현금');
  await page.keyboard.press('Enter');
  await page.keyboard.type('7777777');
  await page.getByText('여러 종목 한 번에 넣기', { exact: true }).click();
  await page.getByLabel('일반 계좌 · 종목 코드와 금액(원)').fill('nope 1');
  await page.getByRole('button', { name: '목록에 추가' }).click();
  const actions = () => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action);
  await expect.poll(actions).toEqual(expect.arrayContaining(['input_search_opened', 'input_holding_added', 'bulk_entry_opened', 'bulk_entry_failed']));
  expect(actions().filter(a => a === 'input_search_opened')).toHaveLength(1);
  expect(JSON.stringify(events)).not.toMatch(/zzqprivate|7777777|7,777,777|nope/);
});

test('a first search made before visitor classification finishes is still counted', async ({ page, baseURL }) => {
  // 첫 검색은 화면이 열리자마자 일어난다. 시험·고객 분류 응답이 늦다고 그 행동을 버리면 퍼널의 첫 단계가 비어 보인다.
  const events = await instrument(page, baseURL!);
  await page.route('https://etforca.com/api/portfolio/session', async route => { await new Promise(r => setTimeout(r, 2000)); await route.fulfill({ json: { is_test: false } }); });
  await page.goto('https://etforca.com/portfolio/');
  await page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' }).focus();
  await expect.poll(() => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action), { timeout: 10000 }).toContain('input_search_opened');
});

test('focus moved by the form itself is not counted as the visitor opening the search', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.route('https://etforca.com/api/portfolio/workbook/catalog', route => route.fulfill({ json: { counts: { KR: 0, US: 1 }, instruments: [{ id: 'US:VOO', name: 'VOO', ticker: 'VOO', code: 'VOO', market: 'US', kind: 'etf' }] } }));
  await page.goto('https://etforca.com/portfolio/');
  await page.getByText('여러 종목 한 번에 넣기', { exact: true }).click();
  await page.getByLabel('일반 계좌 · 종목 코드와 금액(원)').fill('VOO 1000000');
  await page.getByRole('button', { name: '목록에 추가' }).click();
  await page.getByRole('textbox', { name: 'VOO 금액' }).press('Enter');
  const search = page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' });
  await expect(search).toBeFocused();
  // 뒤에 일어난 행동이 도착한 걸 확인한 뒤에 부재를 본다 — 이벤트는 묶음으로 보내져 늦게 올 수 있다.
  await page.keyboard.press('Escape'); // 포커스된 검색창의 후보 목록이 아래 '여러 종목' 토글을 덮는다
  await page.getByText('여러 종목 한 번에 넣기', { exact: true }).click();
  await expect(page.locator('details.bulk-entry')).not.toHaveAttribute('open', '');
  await page.waitForTimeout(100); // toggle 이벤트는 비동기라, 닫힘이 처리되기 전에 다시 열면 한 번으로 합쳐진다
  await page.getByText('여러 종목 한 번에 넣기', { exact: true }).click();
  const actions = () => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action);
  await expect.poll(() => actions().filter(a => a === 'bulk_entry_opened').length).toBe(2);
  expect(actions()).not.toContain('input_search_opened');
  // 코드가 옮긴 검색창에 방문자가 바로 입력하면 그때는 방문자의 첫 검색이다(포커스 이벤트 없이 입력만으로 세야 한다).
  await page.getByRole('textbox', { name: 'VOO 금액' }).press('Enter');
  await expect(search).toBeFocused();
  await page.keyboard.type('V');
  await expect.poll(actions).toContain('input_search_opened');
});

test('opening the folded request box is counted so its use can be judged', async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const events = await instrument(page, baseURL!);
  await page.goto('https://etforca.com/portfolio/');
  await page.getByText('요청사항 적기 · 선택', { exact: true }).click();
  await page.getByLabel('요청사항', { exact: true }).fill('private-request-text');
  await expect.poll(() => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action)).toContain('request_box_opened');
  expect(JSON.stringify(events)).not.toContain('private-request-text');
});

test('a request box that starts open on desktop is not reported as opened by the visitor', async ({ page, baseURL }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const events = await instrument(page, baseURL!);
  await page.goto('https://etforca.com/portfolio/');
  await expect(page.getByLabel('요청사항', { exact: true })).toBeVisible();
  // 뒤 행동이 도착한 걸 확인한 뒤 부재를 본다(이벤트는 묶음으로 늦게 올 수 있다).
  await page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' }).focus();
  const actions = () => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action);
  await expect.poll(actions).toContain('input_search_opened');
  expect(actions()).not.toContain('request_box_opened');
});

test('a no-result search is counted once per search, not once per keystroke flicker', async ({ page, baseURL }) => {
  // 한글 조합 중에는 결과 0건이 깜빡인다. 한 번의 검색을 여러 번으로 세면 '못 찾음' 빈도가 부풀려진다.
  const events = await instrument(page, baseURL!);
  await page.route('https://etforca.com/api/portfolio/workbook/catalog', route => route.fulfill({ json: { counts: { KR: 0, US: 1 }, instruments: [{ id: 'US:VOO', name: 'VOO', ticker: 'VOO', code: 'VOO', market: 'US', kind: 'etf' }] } }));
  await page.goto('https://etforca.com/portfolio/');
  const search = page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' });
  for (const text of ['V', 'Vq', 'V', 'Vq', 'V', 'Vq']) await search.fill(text);
  await search.blur();
  await search.fill('Vqx'); // 닫았다가 0건인 채로 이어 친 검색도 새 검색이다
  await page.keyboard.press('Escape'); // 후보 목록이 아래 토글을 덮는다
  await page.getByText('여러 종목 한 번에 넣기', { exact: true }).click(); // 뒤 행동이 도착한 뒤 개수를 본다
  const actions = () => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action);
  await expect.poll(actions).toContain('bulk_entry_opened');
  expect(actions().filter(a => a === 'instrument_search_empty')).toHaveLength(2);
});

test('starting from recommendations is counted so the no-search path is visible', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.goto('https://etforca.com/portfolio/');
  await page.getByRole('button', { name: /종목부터 추천받기/ }).click();
  // 돌아와 다시 눌러도 같은 초안의 추천 시작은 한 번이다(횟수가 부풀지 않게).
  await page.getByRole('button', { name: '앞 질문으로' }).click();
  await page.getByRole('button', { name: /종목부터 추천받기/ }).click();
  await page.getByRole('button', { name: '앞 질문으로' }).click();
  await page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' }).focus(); // 뒤 행동이 도착한 뒤 개수를 본다
  const actions = () => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action);
  await expect.poll(actions).toContain('input_search_opened');
  expect(actions().filter(a => a === 'recommend_started')).toHaveLength(1);
});

test('a recommendation click that cannot save is not counted as a started recommendation', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.route('https://etforca.com/api/portfolio/drafts/**', route => route.fulfill({ status: 503, json: {} }));
  await page.goto('https://etforca.com/portfolio/');
  await page.getByRole('button', { name: /종목부터 추천받기/ }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('heading', { name: '내 포트폴리오를 더 좋게 바꿔볼까요?' })).toBeVisible();
  await page.getByRole('combobox', { name: '일반 계좌에 담은 종목 찾기' }).focus(); // 뒤 행동이 도착한 뒤 부재를 본다
  const actions = () => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action);
  await expect.poll(actions).toContain('input_search_opened');
  expect(actions()).not.toContain('recommend_started');
});

test('opening the example result is counted', async ({ page, baseURL }) => {
  const events = await instrument(page, baseURL!);
  await page.goto('https://etforca.com/portfolio/');
  await page.getByRole('button', { name: '예시 결과 보기' }).click();
  await expect.poll(() => events.filter(e => e.event === 'portfolio_action').map(e => e.properties.action)).toContain('example_report_opened');
});
