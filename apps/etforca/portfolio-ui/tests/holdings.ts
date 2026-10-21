import type { Page } from '@playwright/test';

// The input starts empty, so flows that only need a valid first step add one cash holding.
export async function addCash(page: Page, amount = '1000000', account = '일반 계좌') {
  await page.getByRole('combobox', { name: `${account}에 담은 ETF 찾기` }).fill('현금');
  await page.getByRole('option', { name: '현금 원화 현금' }).click();
  await page.getByRole('textbox', { name: '현금 금액', exact: true }).fill(amount);
}
