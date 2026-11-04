import type { Page } from '@playwright/test';
import { addHolding, startInput } from '../scripts/fill-form.mjs';
export { addHolding, startInput };

// The input starts empty, so flows that only need a valid first step add one cash holding.
export async function addCash(page: Page, amount = '1000000', account = '일반 계좌') {
  await addHolding(page, '현금', '현금', amount, account);
}
// 1/3 → 2/3 → 3/3 까지 필수 답만 채운다(매달 넣지 않음 · 정한 때 없음).
export async function toAsk(page: Page) {
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '안 넣어요', exact: true }).click();
  await page.getByRole('button', { name: /^정한 때 없어요/ }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
}
