import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { addCash } from './holdings';

async function toPlan(page: Page) {
  await page.goto('/portfolio/?preview=input');
  await addCash(page);
  await page.getByRole('button', { name: '다음', exact: true }).click();
}
const tile = (page: Page, account: string) => page.getByRole('button', { name: new RegExp('^' + account) });

// 가입할 수 없는 계좌를 추천받으면 실행할 수 없다. 서버도 같은 답에서 거절하므로(contracts.py eligible_targets) 화면이 먼저 막고 까닭을 적는다.
test('ineligible ISA answers explain why, clear the selection and recover when the answer changes', async ({ page }) => {
  await toPlan(page);
  await tile(page, 'ISA').click();
  const residence = page.getByRole('group', { name: '국내에 살고 있나요?' });
  await residence.getByRole('button', { name: '아니요' }).click();
  await expect(tile(page, 'ISA')).toBeDisabled();
  await expect(tile(page, 'ISA')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#why-ISA')).toHaveText('국내 거주자만 가입할 수 있어요');
  await expect(page.locator('#why-ISA')).toHaveCSS('color', 'rgb(210, 32, 48)');
  // 빠진 ISA 의 남은 질문(금융소득종합과세)이 비어 있어도 넘어가는 것을 막지 않는다.
  await page.getByRole('button', { name: '안 넣어요', exact: true }).click();
  await page.getByRole('button', { name: /^3년 안/ }).click();
  await expect(page.getByRole('button', { name: '다음', exact: true })).toBeEnabled();
  await expect(tile(page, '연금저축')).toBeEnabled();
  await residence.getByRole('button', { name: '예' }).click();
  await expect(tile(page, 'ISA')).toBeEnabled();
  await page.getByRole('group', { name: /금융소득종합과세/ }).getByRole('button', { name: '예' }).click();
  await expect(tile(page, 'ISA')).toBeDisabled();
  await page.screenshot({ path: test.info().outputPath('eligibility.png'), fullPage: true });
});

test('IRP distinguishes no qualification from retirement benefits and unknown information', async ({ page }) => {
  await toPlan(page);
  await tile(page, 'IRP').click();
  const answers = page.getByRole('group', { name: 'IRP 가입 조건' });
  await answers.getByRole('button', { name: '둘 다 아니에요' }).click();
  await expect(tile(page, 'IRP')).toBeDisabled();
  await expect(page.locator('#why-IRP')).toHaveText('소득이나 퇴직금이 있어야 해요');
  await answers.getByRole('button', { name: '퇴직금을 받을 예정이에요' }).click();
  await expect(tile(page, 'IRP')).toBeEnabled();
  await answers.getByRole('button', { name: '모르겠어요' }).click();
  await expect(tile(page, 'IRP')).toBeEnabled();
});

// 옛 절세 창에서 저장한 답(나이·근로소득)은 새 화면에서 묻지 않아도 규칙은 그대로 적용된다 — 서버가 거절할 계좌를 보내지 않는다.
test('a restored old tax profile keeps its eligibility and never sends an ineligible target', async ({ page }) => {
  let saved: any;
  await page.route('**/api/portfolio/**', route => {
    if (route.request().url().includes('/drafts/')) saved = route.request().postDataJSON();
    return route.fulfill({ json: route.request().url().endsWith('/catalog') ? { counts: { KR: 0, US: 0 }, instruments: [] } : {} });
  });
  await page.goto('/portfolio/');
  await addCash(page);
  await page.evaluate(() => {
    const draft = JSON.parse(sessionStorage.getItem('edge-portfolio-form-v2')!);
    draft.tax = { residence: '대한민국 거주자', age: '15~18세', income: '소득 없음', financial_income_tax: '아니요', previous_year_earned_income: '아니요', recommended_accounts: ['ISA'], accounts: [] };
    draft.selected = ['일반 계좌', 'ISA', '연금저축']; draft.step = 1; draft.how = '이미 다 매수한 상태에요'; draft.horizon = '3년 안';
    sessionStorage.setItem('edge-portfolio-form-v2', JSON.stringify(draft));
  });
  await page.reload();
  await expect(page.locator('#why-ISA')).toHaveText('만 19세 미만은 지난해 근로소득이 있어야 해요');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await expect.poll(() => saved?.preferences.tax_profile?.recommended_accounts).toEqual(['연금저축']);
  expect(saved.preferences.accounts).toEqual(['일반 계좌', '연금저축']);
});
