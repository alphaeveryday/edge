import { test, expect } from '@playwright/test';
import { addCash, addHolding, startInput, toAsk } from './holdings';

test('unit changes preserve independent amounts and percentages through refresh', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '345');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await page.getByLabel('현금 비중', { exact: true }).fill('100');
  await page.locator('#total').fill('80');
  await page.reload();
  await expect(page.getByLabel('현금 비중', { exact: true })).toHaveValue('100');
  await expect(page.locator('#total')).toHaveValue('80');
  await page.getByRole('button', { name: '금액', exact: true }).click();
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('345');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await expect(page.getByLabel('현금 비중', { exact: true })).toHaveValue('100');
});

test('refresh resumes the current step and preview reset cannot erase a customer draft', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('edge-portfolio-form-v2', 'customer draft'));
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '777');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '50만', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('progressbar', { name: '3단계 중 2단계 앞으로의 계획' })).toBeVisible();
  await expect(page.getByLabel('매달 넣을 돈(만 원)')).toHaveValue('50');
  await page.getByRole('button', { name: '앞 단계로' }).click();
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('777');
  await page.getByRole('button', { name: '테스트 초기화' }).click();
  await expect(page.getByRole('button', { name: '내 포트폴리오 넣기' })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('edge-portfolio-form-v2'))).toBe('customer draft');
});

// 고른 계좌를 다시 빼면 그 계좌의 확인 질문도 조건에서 빠진다(답하지 않은 질문이 남아 막지 않는다).
test('an account chosen and then removed leaves no pending eligibility question', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.locator('.fl-step')).toBeFocused();
  await page.getByRole('button', { name: '안 넣어요', exact: true }).click();
  await page.getByRole('button', { name: /^3년 안/ }).click();
  await page.getByRole('button', { name: /^ISA/ }).click();
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: /^ISA/ }).click();
  await expect(page.getByRole('group', { name: '국내에 살고 있나요?' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeEnabled();
});

test('duplicate ETF selection explains rejection and keeps the search for correction', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  const search = page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' });
  await addHolding(page, '현금', '현금');
  await expect(page.getByRole('alert')).toContainText('이미 담은 종목');
  await expect(search).toHaveValue('현금');
  await expect(page.locator('.fl-hold')).toHaveCount(1);
});

// 계좌 바꾸기는 그 계좌의 종목을 통째로 옮기되, 옮길 수 없는 계좌(미국 상장 → ISA)는 고를 수 없다. 금액은 그대로다.
test('moving an account group keeps the values and refuses accounts the holdings cannot be in', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addHolding(page, '069500', 'KODEX 200', '200');
  await addHolding(page, 'VOO', 'Vanguard S&P 500 ETF', '300');
  await page.getByRole('button', { name: '일반 계좌 계좌 바꾸기' }).click();
  const sheet = page.getByRole('dialog', { name: /어느 계좌로 옮길까요/ });
  await expect(sheet.getByRole('button', { name: /^ISA/ })).toBeDisabled();
  // 키보드 없이(터치) 닫을 수 있어야 한다.
  await sheet.getByRole('button', { name: '계좌 고르기 닫기' }).click();
  await expect(sheet).toBeHidden();
  await page.getByRole('button', { name: 'Vanguard S&P 500 ETF 빼기' }).click();
  await page.getByRole('button', { name: '일반 계좌 계좌 바꾸기' }).click();
  await sheet.getByRole('button', { name: /^ISA/ }).click();
  await sheet.getByRole('button', { name: '이 계좌로 옮기기' }).click();
  await expect(page.locator('.fl-group')).toHaveCount(1);
  await expect(page.locator('.fl-group')).toContainText('ISA');
  await expect(page.getByLabel('KODEX 200 금액', { exact: true })).toHaveValue('200');
});

// 마지막 남은 계좌는 해제할 수 없다 — 계좌가 빈 초안은 옛 번들(롤백)이 통째로 버리고, 쓸 계좌 없는 분석은 없다.
test('the last remaining account cannot be unselected', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await page.getByRole('button', { name: '아직 투자 전이에요' }).click();
  await expect(page.getByRole('button', { name: /^일반 계좌/ })).toBeDisabled();
  await page.getByRole('button', { name: /^연금저축/ }).click();
  await expect(page.getByRole('button', { name: /^일반 계좌/ })).toBeEnabled();
  await page.getByRole('button', { name: /^일반 계좌/ }).click();
  await expect(page.getByRole('button', { name: /^연금저축/ })).toBeDisabled();
});

// 한도에 걸린 입력을 고친 뒤 처음 화면에 다녀와도 고친 값이 남는다(거절된 요청으로 되돌리지 않는다).
test('an input edited after a limit screen keeps the edits across the start screen', async ({ page }) => {
  await page.route('**/api/portfolio/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/analyses') && route.request().method() === 'POST') return route.fulfill({ status: 429, json: { detail: 'QUEUE_FULL' } });
    return route.fulfill({ json: path.endsWith('/catalog') ? { counts: { KR: 0, US: 0 }, instruments: [] } : path.endsWith('/latest') ? null : {} });
  });
  await page.goto('/portfolio/');
  await addCash(page, '1000');
  await toAsk(page);
  await page.getByRole('button', { name: '분석 시작하기' }).click();
  await expect(page.getByRole('heading', { name: /분석 요청이/ })).toBeVisible();
  await page.getByRole('button', { name: '입력 고치기' }).click();
  await page.getByLabel('현금 금액', { exact: true }).fill('2000');
  await page.getByRole('button', { name: '처음 화면으로' }).click();
  await page.getByRole('button', { name: '내 포트폴리오 넣기' }).click();
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('2,000');
});

test('malformed stored form does not prevent a new analysis', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('edge-preview-form-v2', '{"step":2,"holdings":null}'));
  await page.goto('/portfolio/?preview=input');
  await startInput(page);
  await expect(page.locator('.fl-hold')).toHaveCount(0);
  await expect(page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' })).toBeVisible();
});

test('Escape offers to keep unsaved asset edits and saving preserves them', async ({ page }) => {
  await page.goto('/portfolio/?preview=dashboard');
  await page.locator('.holding-edit').first().click();
  await page.getByLabel('보유 수량', { exact: true }).fill('401');
  await page.keyboard.press('Escape');
  await expect(page.locator('.ds-confirm-dialog')).toBeVisible();
  await page.locator('.ds-confirm-dialog').getByRole('button', { name: '취소', exact: true }).click();
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('401');
  await page.getByRole('dialog').getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.locator('.dashboard-save-status')).toContainText('저장했어요');
  await page.locator('.holding-edit').first().click();
  await expect(page.getByLabel('보유 수량', { exact: true })).toHaveValue('401');
});

test('catalog retry keeps entered values and recovers search without refreshing', async ({ page }) => {
  let fail = true;
  await page.route('**/api/portfolio/session', r => r.fulfill({ json: {} }));
  await page.route('**/api/portfolio/workbook/catalog', r => fail ? r.fulfill({ status: 503, json: {} }) : r.fulfill({ json: { counts: { KR: 0, US: 1 }, instruments: [{ id: 'US:VOO', name: 'VOO', ticker: 'VOO', code: 'VOO', market: 'US', kind: 'etf' }] } }));
  await page.goto('/portfolio/');
  await startInput(page);
  // 목록을 못 받아도 많이 담는 종목·현금은 바로 담을 수 있다.
  await expect(page.getByText('종목 목록을 불러오지 못했어요')).toBeVisible();
  await page.getByRole('button', { name: '현금 담기', exact: true }).click();
  await page.getByRole('button', { name: '이 계좌에 담기' }).click();
  await page.getByLabel('현금 금액', { exact: true }).fill('250');
  fail = false;
  await page.getByRole('button', { name: /다시 불러오기/ }).click();
  await page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' }).fill('VOO');
  await expect(page.getByRole('button', { name: 'VOO 담기' })).toBeVisible();
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('250');
});

test('invalid numeric precision is explained before an API submission fails', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  // Amounts copied from a brokerage app keep only their digits; percentages still reject extra precision.
  await addCash(page, '1,234,567원');
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('1,234,567');
  await page.getByRole('button', { name: '비중', exact: true }).click();
  await page.getByLabel('현금 비중', { exact: true }).fill('12.345');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
  await expect(page.locator('#step-guidance')).toContainText('소수 둘째 자리');
  await page.getByLabel('현금 비중', { exact: true }).fill('100');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeEnabled();
});

test('the 20-holding limit is visible instead of silently rejecting a selection', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await startInput(page);
  await page.evaluate(() => {
    const key = 'edge-preview-form-v2';
    const draft = JSON.parse(sessionStorage.getItem(key)!);
    draft.holdings = Array.from({ length: 20 }, (_, i) => ({ name: 'ETF ' + i, account: '일반 계좌', instrument_id: 'US:' + i, amount_krw: '10', kind: 'etf' }));
    sessionStorage.setItem(key, JSON.stringify(draft));
  });
  await page.reload();
  await page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' }).fill('VOO');
  await page.getByRole('button', { name: 'Vanguard S&P 500 ETF 담기' }).click();
  await expect(page.getByRole('alert')).toContainText('최대 20종목');
  await expect(page.locator('.fl-hold')).toHaveCount(20);
});

test('storage failure preserves retry identity and still reaches an accepted analysis', async ({ page }) => {
  const keys: string[] = [];
  await page.route('**/api/portfolio/**', r => {
    if (r.request().url().endsWith('/analyses') && r.request().method() === 'POST') { keys.push(r.request().headers()['idempotency-key']); if (keys.length === 1) return r.fulfill({ status: 503, json: {} }); return r.fulfill({ json: { id: 'accepted', state: 'queued' } }); }
    return r.fulfill({ json: r.request().url().includes('/analyses/') ? { id: 'accepted', state: 'queued' } : { instruments: [], counts: { KR: 0, US: 0 } } });
  });
  await page.addInitScript(() => { Storage.prototype.setItem = function () { throw new DOMException('denied', 'SecurityError'); }; });
  await page.goto('/portfolio/');
  await startInput(page);
  await expect(page.getByRole('alert')).toContainText('입력을 임시 보관할 수 없어요');
  // 보관할 수 없으니 처음 화면으로 나가면 쓴 것이 사라진다 — 나가는 길을 막는다.
  await expect(page.getByRole('button', { name: '처음 화면으로' })).toBeDisabled();
  await addCash(page, '99');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '안 넣어요', exact: true }).click();
  await page.getByRole('button', { name: /^정한 때 없어요/ }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '분석 시작하기' }).click();
  await expect(page.getByText('요청을 처리하지 못했어요. 연결 상태를 확인해 주세요.')).toBeVisible();
  await page.getByRole('button', { name: '접수 다시 확인' }).click();
  await expect(page.locator('.progress')).toBeVisible();
  expect(keys).toHaveLength(2); expect(keys[0]).toBe(keys[1]);
  await expect(page.getByText(/분석 결과를 자동 복원할 수 없어요/)).toBeVisible();
});

for (const matching of [false, true]) test(`a completed job clears only its own submitted draft (${matching})`, async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '345');
  await page.evaluate(matching => {
    const draft = JSON.parse(sessionStorage.getItem('edge-preview-form-v2')!);
    if (matching) draft.submittedJobId = 'other-tab-job';
    sessionStorage.setItem('edge-portfolio-form-v2', JSON.stringify(draft));
    localStorage.setItem('edge-portfolio-job', 'other-tab-job');
    localStorage.setItem('edge-portfolio-feedback:other-tab-job', 'done');
  }, matching);
  await page.route('**/api/portfolio/**', r => r.fulfill({ json: r.request().url().includes('/analyses/')
    ? { id: 'other-tab-job', state: 'succeeded', result: { sections: { opinion: { paragraphs: ['테스트 결론'], evidence: [], limitations: [] } }, sources: [] } }
    : { counts: { KR: 0, US: 0 }, instruments: [] } }));
  await page.goto('/portfolio/');
  await page.getByRole('button', { name: '새 포트폴리오 분석하기' }).click();
  if (matching) await expect(page.locator('.fl-hold')).toHaveCount(0);
  else await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('345');
});

test('a restored holding cannot be hidden outside the editable account list', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  await page.evaluate(() => {
    const key = 'edge-preview-form-v2', draft = JSON.parse(sessionStorage.getItem(key)!);
    draft.holdings[0].account = 'ISA'; draft.holdings[0].amount_krw = '345';
    sessionStorage.setItem(key, JSON.stringify(draft));
  });
  await page.reload();
  await startInput(page);
  await expect(page.locator('.fl-hold')).toHaveCount(0);
});

test('draft cleanup denial is explained instead of losing the current input', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '345');
  await page.reload();
  await page.evaluate(() => { Storage.prototype.removeItem = function () { throw new DOMException('denied', 'SecurityError'); }; });
  await page.getByRole('button', { name: '새로 작성', exact: true }).click();
  await page.getByRole('button', { name: '새로 시작하기', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('임시 입력을 지우지 못했어요');
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('345');
});
