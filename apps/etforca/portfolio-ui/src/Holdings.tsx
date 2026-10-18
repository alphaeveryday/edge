import { useState } from 'react';
import { Button } from './design-system/components';
import { classifyAsset } from './assetClassification';
import { money, type Asset, type Quote, type Forecast } from './workbook';

const number = (n: number | null | undefined) => n == null ? '—' : n.toLocaleString('ko-KR', { maximumFractionDigits: 4 });
const signed = (n: number | null | undefined) => n == null ? '—' : `${n > 0 ? '+' : ''}${money(n)}`;
export function Holdings({ assets, quotes, forecasts, missing, total, fx, amountOf, disabled, onEdit }: {
  assets: Asset[]; quotes: Map<string, Quote>; forecasts: Forecast[]; missing: string[];
  total: number | null; fx: number | null; amountOf: (a: Asset) => number | null; disabled: boolean; onEdit: (a: Asset) => void;
}) {
  const [descending, setDescending] = useState(true);
  const rows = [...assets].sort((a, b) => {
    const av = amountOf(a), bv = amountOf(b);
    if (av === null) return bv === null ? 0 : 1;
    if (bv === null) return -1;
    return (bv - av) * (descending ? 1 : -1);
  });
  function price(value: number | null | undefined, a: Asset) {
    if (value == null) return '—';
    return a.currency === 'USD' ? <>{fx === null ? '—' : money(value * fx)}<small>${number(value)}</small></> : money(value);
  }
  return <div className="holdings-scroll" role="region" aria-label="보유 종목 상세, 좌우로 이동" tabIndex={0}><table className="holdings-table"><thead><tr>
    <th scope="col">종목</th><th scope="col" aria-sort={descending ? 'descending' : 'ascending'}><Button onClick={() => setDescending(!descending)}>비중 {descending ? '↓' : '↑'}</Button></th>
    {['주식수', '평단가', '현재가', '평가금액', '수익률', '평가손익', '배당률', '예상 연배당금', '배당주기', '투자 지역·테마'].map(t => <th scope="col" key={t}>{t}</th>)}
  </tr></thead><tbody>{rows.map(a => {
    const q = quotes.get(a.id), amount = amountOf(a), gain = q?.profit ?? null;
    const basis = amount !== null && gain !== null ? amount - gain : null;
    const pct = gain !== null && basis !== null && basis > 0 ? gain / basis * 100 : null;
    const cash = a.category === '현금', classification = classifyAsset(a);
    const annual = missing.includes(a.id) ? null : forecasts.filter(d => d.asset_id === a.id).reduce((s, d) => s + d.amount, 0);
    const tone = gain === null ? '' : gain >= 0 ? 'gain' : 'loss';
    return <tr key={a.id}>
      <th scope="row"><Button className="holding-edit" disabled={disabled} onClick={() => onEdit(a)}><b>{a.name}</b><small>{a.instrument_id?.split(':')[1]} · {a.account}</small>{q?.estimated_quantity && <small>금액 기준 추적</small>}</Button></th>
      <td data-label="비중">{amount !== null && total !== null && total > 0 ? (amount / total * 100).toFixed(1) + '%' : '—'}</td>
      <td data-label="주식수">{cash ? '—' : number(q?.quantity)}{q?.estimated_quantity && <small>추정 수량</small>}</td>
      <td data-label="평단가">{cash ? '—' : price(q?.average, a)}</td>
      <td data-label="현재가">{cash ? '—' : price(q?.price, a)}{q?.price_date && <small>{q.price_date}</small>}</td>
      <td data-label="평가금액">{money(amount)}</td>
      <td data-label="수익률" className={tone}>{pct === null ? '—' : `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`}</td>
      <td data-label="평가손익" className={tone}>{signed(gain)}</td>
      <td data-label="배당률" className="dividend-number">{cash ? '—' : q?.dividend_yield_pct == null ? '—' : `${q.dividend_yield_pct.toFixed(2)}%`}</td>
      <td data-label="예상 연배당금">{cash ? '—' : money(annual)}</td>
      <td data-label="배당주기"><span className="dividend-cycle">{cash ? '—' : q?.dividend_cycle ?? '미확인'}</span></td>
      <td data-label="투자 지역·테마">{classification.region}<small>{classification.theme}</small></td>
    </tr>;
  })}</tbody></table></div>;
}
