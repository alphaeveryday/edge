import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { startInput } from './holdings';

const data = (name: string) => JSON.parse(readFileSync(new URL(`../../portfolio-worker/src/edge_portfolio/data/${name}.json`, import.meta.url), 'utf8'));
// 서버 /workbook/catalog 와 같은 순서(ETF → 개별주)의 실제 목록. ETF가 먼저라서 예전에는 이름이 같은 주식이 뒤로 밀렸다.
const etf = data('etf_catalog'), stock = data('stock_catalog');
const catalog = { ...etf, instruments: [...etf.instruments, ...stock.instruments] };

test.beforeEach(async ({ page }) => {
  await page.route('**/api/portfolio/**', route => route.fulfill({ json: route.request().url().endsWith('/catalog') ? catalog : {} }));
  await page.goto('/portfolio/');
  await startInput(page);
});

// 입력 화면은 8개만 보여 주고 Enter 는 첫 줄을 담는다. 이름을 정확히 쳤는데 그 종목이 안 보이거나 다른 ETF가 담기면 안 된다.
test('an exact name, ticker or code is the first result even when many ETFs contain it', async ({ page }) => {
  const search = page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' });
  const first = page.getByRole('list', { name: '찾은 종목' }).getByRole('listitem').first();
  for (const [query, name] of [['삼성전자', '삼성전자'], ['SK하이닉스', 'SK하이닉스'], ['현대차', '현대차'], ['KODEX 200', 'KODEX 200'], ['kodex200', 'KODEX 200'], ['005930', '삼성전자'], ['TSLA', 'Tesla, Inc.  - Common Stock']]) {
    await search.fill(query);
    await expect(first.getByRole('button', { name: `${name} 담기`, exact: true }), query).toBeVisible();
  }
});

test('names that start with the query come before names that only contain it', async ({ page }) => {
  await page.getByRole('searchbox', { name: '종목 이름이나 코드로 찾기' }).fill('삼성전자');
  const rows = page.getByRole('list', { name: '찾은 종목' }).getByRole('listitem');
  // 삼성전자(정확히 같음) → 삼성전자우(이름이 그 글자로 시작함) → 이름 가운데에 삼성전자가 든 ETF.
  await expect(rows.nth(0).getByRole('button', { name: '삼성전자 담기', exact: true })).toBeVisible();
  await expect(rows.nth(1).getByRole('button', { name: '삼성전자우 담기', exact: true })).toBeVisible();
  await expect(rows).toHaveCount(8);
});
