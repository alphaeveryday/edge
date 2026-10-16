import type { Execution } from './api';

type Amounts = Partial<Pick<Execution['rows'][number], 'buy_krw' | 'sell_krw' | 'cash_used_krw'>>;
export const hasTradeAmounts = (row?: Amounts): row is Required<Amounts> => !!row &&
  [row.buy_krw, row.sell_krw, row.cash_used_krw].every(value => typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) &&
  Number(row.buy_krw) >= 0 && Number(row.sell_krw) >= 0;

export function TradeAmount({ row }: { row: Required<Amounts> }) {
  const buy = Number(row.buy_krw), sell = Number(row.sell_krw), cash = Number(row.cash_used_krw);
  const side = buy > 0 ? 'buy' : sell > 0 ? 'sell' : 'hold';
  const amount = buy || sell || Math.abs(cash);
  const label = buy > 0 ? '매수' : sell > 0 ? '매도' : cash > 0 ? '현금 투입' : cash < 0 ? '현금 확보' : '보유 유지';
  return <p className="trade-amount" data-trade={side}>{amount > 0 && <strong>{amount.toLocaleString('ko-KR')}원</strong>}<span>{label}</span></p>;
}
