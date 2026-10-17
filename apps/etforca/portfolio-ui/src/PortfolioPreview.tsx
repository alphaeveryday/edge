import { chartPalette } from './design-system/components';
import type { Holding } from './api';

const colors = chartPalette;
export function PortfolioPreview({ holdings, mode }: { holdings: Holding[]; mode: string }) {
  if (!holdings.length) return null;
  const total = holdings.reduce((sum, h) => sum + Number(h.amount_krw), 0);
  const known = mode !== 'names' && total > 0 && holdings.every(h => Number(h.amount_krw) > 0);
  let offset = 0;
  return <div className="ans mine portfolio-preview"><small>내 포트폴리오</small><div className="mixviz"><div className="donut md" role="img" aria-label="현재 입력한 ETF와 현금 비중"><svg viewBox="0 0 42 42">{holdings.map((h, i) => { const weight = known ? Number(h.amount_krw) / total * 100 : 100 / holdings.length; const start = offset; offset += weight; return <circle key={i} cx="21" cy="21" r="15.915" fill="none" stroke={colors[i % colors.length]} strokeWidth="6.4" strokeDasharray={`${Math.max(0, weight - .8)} ${100 - Math.max(0, weight - .8)}`} strokeDashoffset={-start} />; })}</svg><div className="donut__c"><b>{mode === 'amount' && known ? `${total.toLocaleString('ko-KR')}만` : `${holdings.length}개`}</b><span>{mode === 'amount' && known ? '원' : 'ETF·현금'}</span></div></div><ul>{holdings.map((h, i) => <li key={i}><i style={{ background: colors[i % colors.length] }} /><span>{h.name}<em>{h.account}</em></span><b>{known ? `${(Number(h.amount_krw) / total * 100).toFixed(1).replace('.0', '')}%` : mode !== 'names' && Number(h.amount_krw) > 0 ? `${Number(h.amount_krw).toLocaleString('ko-KR')}${mode === 'amount' ? '만 원' : '%'}` : '미입력'}</b></li>)}</ul></div>{!known && <p className="hint sm">금액이나 비율을 입력하면 실제 비중을 보여드려요.</p>}</div>;
}
