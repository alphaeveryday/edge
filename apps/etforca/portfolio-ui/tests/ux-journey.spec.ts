import { test, expect } from '@playwright/test';
import { addCash, openRequest } from './holdings';

test('unit changes preserve independent amounts and percentages through refresh', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page, '345');
  await page.getByRole('button', { name: '비율', exact: true }).click();
  await page.getByLabel('현금 비율', { exact: true }).fill('100');
  await page.reload();
  await expect(page.getByLabel('현금 비율', { exact: true })).toHaveValue('100');
  await page.getByRole('button', { name: '금액', exact: true }).click();
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('345');
  await page.getByRole('button', { name: '비율', exact: true }).click();
  await expect(page.getByLabel('현금 비율', { exact: true })).toHaveValue('100');
});

test('refresh resumes the current step and preview reset cannot erase a customer draft', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('edge-portfolio-form-v2', 'customer draft'));
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  await openRequest(page);
  await page.getByLabel('요청사항', { exact: true }).fill('반도체 테마 유지');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '매달 꾸준히', exact: true }).click();
  await page.getByLabel('추가 투자금액').fill('50');
  await page.reload();
  await expect(page.getByLabel('추가 투자금액')).toHaveValue('50');
  await expect(page.getByLabel('전체 5단계 중 2단계')).toBeVisible();
  await page.getByRole('button', { name: '앞 질문으로' }).click();
  await expect(page.getByLabel('요청사항', { exact: true })).toHaveValue('반도체 테마 유지');
  await page.getByRole('button', { name: '테스트 초기화' }).click();
  await expect(page.getByLabel('요청사항', { exact: true })).toHaveValue('');
  expect(await page.evaluate(() => sessionStorage.getItem('edge-portfolio-form-v2'))).toBe('customer draft');
});

test('cancelling optional tax details never makes them a prerequisite', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  await page.locator('.tax-trigger').click();
  await page.getByRole('radio', { name: '대한민국 거주자', exact: true }).check();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect(page.getByRole('heading', { name: '어떻게 투자할 계획인가요?' })).toBeVisible();
  await expect(page.locator('.step')).toBeFocused();
});

test('duplicate ETF selection explains rejection and keeps the search for correction', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  const search = page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' });
  await search.fill('현금');
  await page.getByRole('option', { name: '현금 원화 현금' }).click();
  await expect(page.getByRole('alert')).toContainText('이미 담은 종목');
  await expect(search).toHaveValue('현금');
  await expect(page.locator('.rows .row')).toHaveCount(1);
});

test('removing an account with holdings can be cancelled without losing its values', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await page.getByRole('button', { name: '+ISA', exact: true }).click();
  await page.getByRole('combobox', { name: 'ISA에 담은 ETF 찾기' }).fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard' }).click();
  await page.getByLabel('Vanguard S&P 500 ETF 금액', { exact: true }).fill('200');
  await page.getByRole('button', { name: 'ISA 지우기' }).click();
  await page.getByRole('button', { name: '취소', exact: true }).click();
  await expect(page.getByLabel('Vanguard S&P 500 ETF 금액', { exact: true })).toHaveValue('200');
});

test('malformed stored form does not prevent a new analysis', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('edge-preview-form-v2', '{"step":2,"holdings":null}'));
  await page.goto('/portfolio/?preview=input');
  await expect(page.locator('.rows .row')).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' })).toBeVisible();
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
  await page.route('**/api/portfolio/catalog', r => fail ? r.fulfill({ status: 503, json: {} }) : r.fulfill({ json: { counts: { KR: 0, US: 1 }, instruments: [{ id: 'US:VOO', name: 'VOO', ticker: 'VOO', code: 'VOO', market: 'US', kind: 'etf' }] } }));
  await page.goto('/portfolio/');
  await addCash(page, '250');
  fail = false;
  await page.getByRole('button', { name: '목록 다시 불러오기' }).click();
  await page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' }).fill('VOO');
  await expect(page.getByRole('option')).toContainText('VOO');
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('250');
});

test('invalid numeric precision is explained before an API submission fails', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  // Amounts copied from a brokerage app keep only their digits; percentages still reject extra precision.
  await addCash(page, '1,234,567원');
  await expect(page.getByLabel('현금 금액', { exact: true })).toHaveValue('1,234,567');
  await page.getByRole('button', { name: '비율', exact: true }).click();
  await page.getByLabel('현금 비율', { exact: true }).fill('12.345');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeDisabled();
  await expect(page.locator('#step-guidance')).toContainText('소수 둘째 자리');
  await page.getByLabel('현금 비율', { exact: true }).fill('100');
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeEnabled();
});

test('the 20-holding limit is visible instead of silently rejecting a selection', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await page.evaluate(() => {
    const key = 'edge-preview-form-v2';
    const draft = JSON.parse(sessionStorage.getItem(key)!);
    draft.holdings = Array.from({ length: 20 }, (_, i) => ({ name: 'ETF ' + i, account: '일반 계좌', instrument_id: 'US:' + i, amount_krw: '10', kind: 'etf' }));
    sessionStorage.setItem(key, JSON.stringify(draft));
  });
  await page.reload();
  await page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' }).fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard' }).click();
  await expect(page.getByRole('alert')).toContainText('최대 20종목');
  await expect(page.locator('.rows .row')).toHaveCount(20);
});

test('storage failure preserves retry identity and still reaches an accepted analysis', async ({ page }) => {
  const keys: string[] = [];
  await page.route('**/api/portfolio/**', r => {
    if (r.request().url().endsWith('/analyses') && r.request().method() === 'POST') { keys.push(r.request().headers()['idempotency-key']); if (keys.length === 1) return r.fulfill({ status: 503, json: {} }); return r.fulfill({ json: { id: 'accepted', state: 'queued' } }); }
    return r.fulfill({ json: r.request().url().includes('/analyses/') ? { id: 'accepted', state: 'queued' } : { instruments: [], counts: { KR: 0, US: 0 } } });
  });
  await page.addInitScript(() => { Storage.prototype.setItem = function () { throw new DOMException('denied', 'SecurityError'); }; });
  await page.goto('/portfolio/');
  await expect(page.getByRole('alert')).toContainText('입력을 임시 보관할 수 없어요');
  await addCash(page, '99');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '아직 고민 중', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '정한 때 없어요', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '내 포트폴리오 분석하기' }).click();
  await expect(page.getByText('요청을 처리하지 못했어요. 연결 상태를 확인해 주세요.')).toBeVisible();
  await page.getByRole('button', { name: '접수 다시 확인' }).click();
  await expect(page.locator('.progress')).toBeVisible();
  expect(keys).toHaveLength(2); expect(keys[0]).toBe(keys[1]);
  await expect(page.getByText(/분석 결과를 자동 복원할 수 없어요/)).toBeVisible();
});

test('confirming account removal does not resurrect values when it is added again', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await page.getByRole('button', { name: '+ISA', exact: true }).click();
  await page.getByRole('combobox', { name: 'ISA에 담은 ETF 찾기' }).fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard' }).click();
  await page.getByLabel('Vanguard S&P 500 ETF 금액', { exact: true }).fill('200');
  await page.getByRole('button', { name: '비율', exact: true }).click();
  await page.getByRole('button', { name: 'ISA 지우기' }).click();
  await page.getByRole('button', { name: '삭제하기', exact: true }).click();
  await expect(page.locator('.step')).toBeFocused();
  await page.getByRole('button', { name: '+ISA', exact: true }).click();
  await page.getByRole('combobox', { name: 'ISA에 담은 ETF 찾기' }).fill('VOO');
  await page.getByRole('option').filter({ hasText: 'Vanguard' }).click();
  await page.getByRole('button', { name: '금액', exact: true }).click();
  await expect(page.getByLabel('Vanguard S&P 500 ETF 금액', { exact: true })).toHaveValue('');
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
  if (matching) await expect(page.locator('.rows .row')).toHaveCount(0);
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
  await expect(page.locator('.rows .row')).toHaveCount(0);
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

test('an automatic popup returns focus to the latest reading position on dismissal', async ({ page }) => {
  await page.goto('/portfolio/?preview=result');
  await page.locator('.evaluation').scrollIntoViewIfNeeded();
  await expect(page.locator('.report-feedback')).toBeVisible();
  const readingPosition = page.locator('.feedback-anchor > .save-portfolio button');
  await readingPosition.focus();
  await page.locator('.report-feedback').getByLabel('뉴스레터 이메일').focus();
  await page.getByRole('button', { name: '평가 팝업 닫기' }).click();
  await expect(readingPosition).toBeFocused();
});
