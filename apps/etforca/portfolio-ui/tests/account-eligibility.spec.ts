import { test, expect } from '@playwright/test';

test('a restored ineligible target cannot silently save as an empty selection', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('edge-preview-form-v2'))).not.toBeNull();
  await page.evaluate(() => {
    const saved = JSON.parse(sessionStorage.getItem('edge-preview-form-v2')!);
    saved.tax = { residence: '비거주자', age: '19~54세', income: '소득 없음', financial_income_tax: '아니요',
      recommended_accounts: ['ISA'], accounts: [] };
    sessionStorage.setItem('edge-preview-form-v2', JSON.stringify(saved));
  });
  await page.reload();
  await page.getByRole('button', { name: '최적의 ISA, IRP 등 세금 혜택과 투자전략까지 정리해드릴까요?' }).click();
  const dialog = page.getByRole('dialog', { name: '나에게 맞는 절세 전략' });
  await expect(dialog.getByRole('checkbox', { name: 'ISA', exact: true })).toBeDisabled();
  await dialog.getByRole('button', { name: '선택한 내용 적용하기', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('전략에 포함할 계좌');
  await expect(dialog).toBeVisible();
});

test('ineligible ISA choices explain why and clear a prior selection without blocking pension savings', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await page.getByRole('button', { name: '최적의 ISA, IRP 등 세금 혜택과 투자전략까지 정리해드릴까요?' }).click();
  const dialog = page.getByRole('dialog', { name: '나에게 맞는 절세 전략' });
  const isa = dialog.getByRole('checkbox', { name: 'ISA', exact: true });
  await isa.check();
  await dialog.getByRole('radio', { name: '15세 미만', exact: true }).check();
  await expect(isa).toBeDisabled();
  await expect(isa).not.toBeChecked();
  await expect(dialog.locator('#eligibility-ISA')).toHaveText('ISA는 만 15세부터 가입할 수 있어요.');
  await expect(dialog.getByRole('checkbox', { name: '연금저축', exact: true })).toBeEnabled();
  await dialog.getByRole('radio', { name: '15~18세', exact: true }).check();
  const earned = dialog.getByRole('group', { name: '직전년도에 과세 대상 근로소득이 있었나요?', exact: true });
  await earned.getByRole('radio', { name: '아니요', exact: true }).check();
  await expect(isa).toBeDisabled();
  await earned.getByRole('radio', { name: '예', exact: true }).check();
  await expect(isa).toBeEnabled();
  await dialog.getByRole('radio', { name: '비거주자', exact: true }).check();
  await expect(isa).toBeDisabled();
  await dialog.getByRole('radio', { name: '대한민국 거주자', exact: true }).check();
  await dialog.getByRole('group', { name: '직전 3개 과세기간 중 금융소득종합과세 대상이었나요?', exact: true }).getByRole('radio', { name: '예', exact: true }).check();
  await expect(isa).toBeDisabled();
  await expect(dialog.locator('#eligibility-ISA')).toHaveCSS('color', 'rgb(210, 32, 48)');
  await dialog.locator('#eligibility-ISA').scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath('eligibility.png'), fullPage: true });
});

test('IRP distinguishes no qualification from retirement benefits and unknown information', async ({ page }) => {
  await page.goto('/portfolio/?preview=input');
  await page.getByRole('button', { name: '최적의 ISA, IRP 등 세금 혜택과 투자전략까지 정리해드릴까요?' }).click();
  const dialog = page.getByRole('dialog', { name: '나에게 맞는 절세 전략' });
  const irp = dialog.getByRole('checkbox', { name: 'IRP', exact: true });
  await irp.check();
  await dialog.getByRole('radio', { name: '해당 없음', exact: true }).check();
  await expect(irp).toBeDisabled();
  await expect(irp).not.toBeChecked();
  await expect(dialog.locator('#eligibility-IRP')).toBeVisible();
  await dialog.getByRole('radio', { name: '퇴직급여 수령 대상', exact: true }).check();
  await expect(irp).toBeEnabled();
  await dialog.getByRole('group', { name: 'IRP 가입 대상에 해당하나요?', exact: true }).getByRole('radio', { name: '확인 필요', exact: true }).check();
  await expect(irp).toBeEnabled();
});
