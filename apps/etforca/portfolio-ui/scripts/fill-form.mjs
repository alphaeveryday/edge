// 8차 입력 세 단계(가진 것 → 계획 → 궁금한 점)를 끝까지 채워 분석을 보낸다. 시험과 live-browser 가 같이 쓴다.
export async function startInput(page) {
  const start = page.getByRole('button', { name: '내 포트폴리오 넣기' }), search = page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' });
  await start.or(search).first().waitFor();
  if (await start.isVisible()) await start.click();
}
export async function addHolding(page, query, name, amount, account = '일반 계좌') {
  await startInput(page);
  await page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' }).fill(query);
  await page.getByRole('button', { name: `${name} 담기`, exact: true }).click();
  const sheet = page.getByRole('dialog', { name: '어느 계좌에 있나요?' });
  await sheet.getByRole('button', { name: new RegExp('^' + account) }).click();
  await sheet.getByRole('button', { name: '이 계좌에 담기' }).click();
  if (amount !== undefined) await page.getByRole('textbox', { name: `${name} 금액`, exact: true }).fill(amount);
}
export async function fillForm(page, { question = '한국 주식 비중이 너무 큰가요? KODEX 200의 기존 전망에서 하락 위험을 확인하고 현재 구성에 어떤 영향이 있는지 알려주세요.' } = {}) {
  await addHolding(page, '069500', 'KODEX 200', '7000000');
  await addHolding(page, '360750', 'TIGER 미국S&P500', '3000000');
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByRole('button', { name: '100만', exact: true }).click();
  await page.getByRole('button', { name: /^10년 넘게 뒤/ }).click();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page.getByLabel('무엇이 가장 궁금하세요?').fill(question);
  await page.getByRole('button', { name: '분석 시작하기' }).click();
}
