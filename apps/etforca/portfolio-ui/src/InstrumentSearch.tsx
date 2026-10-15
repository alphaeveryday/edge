import { useId, useMemo, useState } from 'react';
import type { Catalog, Instrument } from './api';

export const cashInstrument: Instrument = { id: 'CASH:KRW', market: 'CASH', ticker: 'KRW', code: 'KRW', name: '현금', kind: 'cash' };
export function InstrumentSearch({ account, catalog, onAdd }: { account: string; catalog: Catalog | null; onAdd: (item: Instrument) => void }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const id = useId();
  const options = useMemo(() => {
    const q = query.toLocaleUpperCase().replace(/\s/g, '');
    const rows = [cashInstrument, ...(catalog?.instruments || [])];
    if (!q) return rows.filter(r => ['CASH:KRW', 'KR:069500', 'KR:360750', 'US:VOO', 'US:QQQM'].includes(r.id));
    return rows.filter(r => (r.name + r.ticker + r.code).toLocaleUpperCase().replace(/\s/g, '').includes(q))
      .sort((a, b) => Number(b.ticker === q) - Number(a.ticker === q)).slice(0, 20);
  }, [query, catalog]);
  function choose(item: Instrument) { onAdd(item); setQuery(''); setOpen(false); setActive(0); }
  return <div className="find instrument-search"><button className="find__plus" aria-label={account + ' ETF 또는 현금 찾기'} onClick={() => { setOpen(!open); document.getElementById(id)?.focus(); }}>+</button>
    <input id={id} className="field" role="combobox" aria-label={account + '에 담은 ETF 찾기'} aria-autocomplete="list" aria-expanded={open} aria-controls={id + '-list'} aria-activedescendant={open && options[active] ? id + '-' + active : undefined} autoComplete="off" placeholder="ETF 추가하기" value={query} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onChange={e => { setQuery(e.target.value); setOpen(true); setActive(0); }} onKeyDown={e => {
      if (e.key === 'Escape') setOpen(false);
      if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(Math.min(active + 1, options.length - 1)); }
      if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(0, active - 1)); }
      if (e.key === 'Enter' && open && options[active]) { e.preventDefault(); choose(options[active]); }
    }} />
    {open && <div className="sug" onMouseDown={e => e.preventDefault()}><div className="catalog-note">ETF·현금만 선택할 수 있어요</div><div role="listbox" id={id + '-list'}>{options.map((item, i) => <button role="option" aria-selected={active === i} id={id + '-' + i} key={item.id} onClick={() => choose(item)}><span>{item.name}<small>{item.kind === 'cash' ? '원화 현금' : `${item.market === 'KR' ? '국내 상장 ETF' : '미국 상장 ETF'} · ${item.ticker}`}</small></span></button>)}</div>{!options.length && <p className="catalog-note">일치하는 ETF가 없어요. 종목코드나 티커로 다시 찾아 주세요.</p>}{catalog && <p className="catalog-note">국내 {catalog.counts.KR.toLocaleString()}개 · 미국 {catalog.counts.US.toLocaleString()}개</p>}</div>}
  </div>;
}
