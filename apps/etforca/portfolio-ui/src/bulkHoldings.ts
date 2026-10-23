import type { Catalog, Holding } from './api';

export function bulkHoldings(text: string, catalog: Catalog, account: string, mode: 'amount' | 'percent'): Holding[] {
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (!lines.length || lines.length > 20) throw new Error('한 줄에 종목과 숫자를 적어 주세요. 최대 20종목까지 담을 수 있어요.');
  const rows = lines.map((line, index) => {
    const match = (mode === 'amount' ? /^(.*?)\s+(\d+|\d{1,3}(?:,\d{3})+)\s*원?$/ : /^(.*?)\s+((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?)\s*%?$/).exec(line);
    if (!match) throw new Error(`${index + 1}번째 줄: 종목 뒤에 ${mode === 'amount' ? '금액(원)' : '비중(%)'}을 적어 주세요.`);
    const name = match[1].trim().toLocaleLowerCase();
    const amount = Number(match[2].replaceAll(',', ''));
    const matches = catalog.instruments.filter(item => [item.id, item.ticker, item.name].some(value => value.toLocaleLowerCase() === name));
    if (matches.length !== 1) throw new Error(`${index + 1}번째 줄: 정확한 종목 코드나 이름을 적어 주세요.`);
    if (!(amount > 0) || amount > (mode === 'percent' ? 100 : 1e13)) throw new Error(`${index + 1}번째 줄의 숫자를 확인해 주세요.`);
    const item = matches[0];
    if (account !== '일반 계좌' && item.market === 'US') throw new Error(`${index + 1}번째 줄: 미국 상장 종목은 일반 계좌에 담아 주세요.`);
    return { name: item.name, instrument_id: item.id, kind: item.kind, account, amount_krw: String(amount) };
  });
  if (new Set(rows.map(row => row.instrument_id)).size !== rows.length) throw new Error('같은 종목은 한 줄로 합쳐 주세요.');
  return rows;
}

// 전체 금액(원)을 비중대로 원 단위로 나눈다. 합계는 전체 금액과 정확히 같고(남는 원은 소수 몫이 큰 종목부터), 비중이 0 인 종목은 0원이다.
// 비중이 양수인데 0원이 되는 종목이 있으면 그 전체 금액으로는 나눌 수 없다는 뜻이다 — 부르는 쪽이 막는다.
export function fromPercents(totalWon: number, percents: number[]): number[] {
  const exact = percents.map(p => totalWon * p / 100), base = exact.map(Math.floor);
  let left = totalWon - base.reduce((sum, n) => sum + n, 0);
  for (const i of exact.map((v, i) => i).sort((a, b) => (exact[b] - base[b]) - (exact[a] - base[a]))) { if (left <= 0) break; if (percents[i] > 0) { base[i]++; left--; } }
  return base;
}

// Whole-portfolio weights in 0.01% steps that sum to exactly 100, as the analysis contract requires.
export function toPercents(amounts: number[]): string[] {
  const total = amounts.reduce((sum, n) => sum + n, 0);
  const units = amounts.map(n => Math.max(1, Math.floor(n / total * 10000)));
  if (units.length) units[units.indexOf(Math.max(...units))] += 10000 - units.reduce((sum, n) => sum + n, 0);
  return units.map(n => String(n / 100));
}
