import { chartPalette } from './design-system/components';
import type { Holding } from './api';

const colors = chartPalette;
// The donut centre is narrow: whole 만, or 억 with one decimal once the total reaches 1억.
const short = (n: number) => n >= 1e8 ? `${(n / 1e8).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억` : `${Math.round(n / 1e4).toLocaleString('ko-KR')}만`;
const won = (n: number) => n >= 10000 ? `${(n / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}만 원` : `${n.toLocaleString('ko-KR')}원`;
export function PortfolioPreview({ holdings, mode }: { holdings: Holding[]; mode: string }) {
  if (!holdings.length) return null;
  const total = holdings.reduce((sum, h) => sum + Number(h.amount_krw), 0);
  const known = mode !== 'names' && total > 0 && holdings.every(h => Number(h.amount_krw) > 0);
  // Percent mode shows the typed weights as-is (a gap means the total is short); rescale only when they exceed 100.
  const raw = mode === 'percent' && total <= 100;
  let offset = 0;
  return <div className="ans mine portfolio-preview"><small>내 포트폴리오</small><div className="mixviz"><div className="donut md" role="img" aria-label="현재 입력한 ETF와 현금 비중"><svg viewBox="0 0 42 42">{holdings.map((h, i) => { const weight = known ? raw ? Number(h.amount_krw) : Number(h.amount_krw) / total * 100 : 100 / holdings.length; const start = offset; offset += weight; return <circle key={i} cx="21" cy="21" r="15.915" fill="none" stroke={colors[i % colors.length]} strokeWidth="6.4" strokeDasharray={`${Math.max(0, weight - .8)} ${100 - Math.max(0, weight - .8)}`} strokeDashoffset={-start} />; })}</svg><div className="donut__c"><b>{mode === 'amount' && known ? total >= 1e4 ? short(total) : total.toLocaleString('ko-KR') : `${holdings.length}개`}</b><span>{mode === 'amount' && known ? '원' : 'ETF·현금'}</span></div></div><ul>{holdings.map((h, i) => <li key={i}><i style={{ background: colors[i % colors.length] }} /><span>{h.name}<em>{h.account}</em></span><b>{mode === 'percent' && Number(h.amount_krw) > 0 ? `${Number(h.amount_krw)}%` : known ? `${(Number(h.amount_krw) / total * 100).toFixed(1).replace('.0', '')}%` : mode === 'amount' && Number(h.amount_krw) > 0 ? won(Number(h.amount_krw)) : '미입력'}</b></li>)}</ul></div>{!known && <p className="hint sm">금액이나 비율을 입력하면 실제 비중을 보여드려요.</p>}</div>;
}
