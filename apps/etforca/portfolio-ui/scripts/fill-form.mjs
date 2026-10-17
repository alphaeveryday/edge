export async function fillForm(page, { interests = ['균형 잡힌 분산'] } = {}) {
  const find = page.getByRole('combobox', { name: '일반 계좌에 담은 ETF 찾기' });
  await find.fill('069500');
  await page.getByRole('option').filter({ hasText: '069500' }).click();
  await page.getByRole('spinbutton', { name: 'KODEX 200 금액' }).fill('700');
  await find.fill('360750');
  await page.getByRole('option').filter({ hasText: '360750' }).click();
  await page.getByRole('spinbutton', { name: 'TIGER 미국S&P500 금액' }).fill('300');
  await page.getByLabel('요청사항', { exact: true }).fill('한국 주식 비중이 너무 큰가요? KODEX 200의 기존 전망에서 하락 위험을 확인하고 현재 구성에 어떤 영향이 있는지 알려주세요.');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '매달 꾸준히', exact: true }).click();
  await page.getByLabel('추가 투자금액').fill('100');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '10년 넘게 뒤', exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  for (const interest of interests) await page.getByRole('button', { name: interest, exact: true }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '내 포트폴리오 분석하기' }).click();
}
