import type { Catalog, Holding } from './api';

export function bulkHoldings(text: string, catalog: Catalog, account: string, mode: 'amount' | 'percent'): Holding[] {
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (!lines.length || lines.length > 20) throw new Error('한 줄에 종목과 숫자를 적어 주세요. 최대 20종목까지 담을 수 있어요.');
  const rows = lines.map((line, index) => {
    const match = (mode === 'amount' ? /^(.*?)\s+((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,4})?)$/ : /^(.*?)\s+((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?)$/).exec(line);
    if (!match) throw new Error(`${index + 1}번째 줄: 종목 뒤에 ${mode === 'amount' ? '금액(만 원)' : '비중(%)'}을 적어 주세요.`);
    const name = match[1].trim().toLocaleLowerCase();
    const amount = Number(match[2].replaceAll(',', ''));
    const matches = catalog.instruments.filter(item => [item.id, item.ticker, item.name].some(value => value.toLocaleLowerCase() === name));
    if (matches.length !== 1) throw new Error(`${index + 1}번째 줄: 정확한 종목 코드나 이름을 적어 주세요.`);
    if (!(amount > 0) || amount > (mode === 'percent' ? 100 : 1000000000)) throw new Error(`${index + 1}번째 줄의 숫자를 확인해 주세요.`);
    const item = matches[0];
    if (account !== '일반 계좌' && item.market === 'US') throw new Error(`${index + 1}번째 줄: 미국 상장 종목은 일반 계좌에 담아 주세요.`);
    return { name: item.name, instrument_id: item.id, kind: item.kind, account, amount_krw: String(amount) };
  });
  if (new Set(rows.map(row => row.instrument_id)).size !== rows.length) throw new Error('같은 종목은 한 줄로 합쳐 주세요.');
  return rows;
}
