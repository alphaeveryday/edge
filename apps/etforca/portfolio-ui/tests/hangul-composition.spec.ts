import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { startInput } from './holdings';

const data = (name: string) => JSON.parse(readFileSync(new URL(`../../portfolio-worker/src/edge_portfolio/data/${name}.json`, import.meta.url), 'utf8'));
const etf = data('etf_catalog'), stock = data('stock_catalog');
const catalog = { ...etf, instruments: [...etf.instruments, ...stock.instruments] };

// 두벌식으로 '삼성전자'를 칠 때 입력칸이 거치는 값([확정된 글, 조합 중인 글자]). 'ㅅ'·'삼ㅅ'·'삼성젅' 같은 미완성 값도 onChange 로 들어온다.
const typing: [string, string][] = [['', 'ㅅ'], ['', '사'], ['', '삼'], ['삼', 'ㅅ'], ['삼', '서'], ['삼', '성'], ['삼성', 'ㅈ'], ['삼성', '저'], ['삼성', '전'], ['삼성', '젅'], ['삼성전', '자']];

test.beforeEach(async ({ page }) => {
  await page.route('**/api/portfolio/**', route => route.fulfill({ json: route.request().url().endsWith('/catalog') ? catalog : {} }));
  await page.goto('/portfolio/');
  await startInput(page);
});

// 운영 검색 0건 이벤트의 절반(283건 중 143건)이 바로 다음에 종목 담기로 이어졌다(2026-10-22~11-06). 다 치기 전 미완성 글자에서
// 결과가 비어 '맞는 종목이 없어요'가 깜빡이고 0건으로 세졌다. 조합 중에도 그 글자로 이어질 종목은 계속 보여야 한다.
test('the list never goes empty while a Korean name is still being composed', async ({ page }) => {
  const search = page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' });
  await search.click();
  const cdp = await page.context().newCDPSession(page);
  let committed = '';
  for (const [done, composing] of typing) {
    if (done !== committed) { await cdp.send('Input.insertText', { text: done.slice(committed.length) }); committed = done; }
    await cdp.send('Input.imeSetComposition', { text: composing, selectionStart: composing.length, selectionEnd: composing.length });
    await expect(search, done + composing).toHaveValue(done + composing);
    await expect(page.getByText('맞는 종목이 없어요'), done + composing).toHaveCount(0);
    await expect(page.getByRole('list', { name: '찾은 종목' }).getByRole('listitem').first(), done + composing).toBeVisible();
  }
  await cdp.send('Input.insertText', { text: '자' });
  await expect(page.getByRole('list', { name: '찾은 종목' }).getByRole('listitem').first().getByRole('button', { name: '삼성전자 담기', exact: true })).toBeVisible();
});
