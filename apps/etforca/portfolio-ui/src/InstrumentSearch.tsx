import { Button, TextInput } from './design-system/components';
import { useEffect, useId, useMemo, useState } from 'react';
import type { Catalog, Instrument } from './api';

export const cashInstrument: Instrument = { id: 'CASH:KRW', market: 'CASH', ticker: 'KRW', code: 'KRW', name: '현금', kind: 'cash' };
export function InstrumentSearch({ account, catalog, onAdd }: { account: string; catalog: Catalog | null; onAdd: (item: Instrument) => string | void }) {
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const id = useId();
  const options = useMemo(() => {
    const q = query.toLocaleUpperCase().replace(/\s/g, '');
    const rows = [cashInstrument, ...((catalog?.instruments || []).filter(i => i.id !== cashInstrument.id))];
    if (!q) return rows.filter(r => ['CASH:KRW', 'KR:069500', 'KR:360750', 'US:VOO', 'US:QQQM'].includes(r.id));
    return rows.filter(r => (r.name + r.ticker + r.code).toLocaleUpperCase().replace(/\s/g, '').includes(q))
      .sort((a, b) => Number(b.ticker === q) - Number(a.ticker === q)).slice(0, 20);
  }, [query, catalog]);
  useEffect(() => {
    const option = open ? document.getElementById(id + '-' + active) : null;
    const list = option?.closest<HTMLElement>('.sug');
    if (!option || !list) return;
    const row = option.getBoundingClientRect(), bounds = list.getBoundingClientRect();
    if (row.top < bounds.top) list.scrollTop -= bounds.top - row.top;
    else if (row.bottom > bounds.bottom) list.scrollTop += row.bottom - bounds.bottom;
  }, [active, open, id]);
  function choose(item: Instrument) { const rejected = onAdd(item); if (rejected) { setError(rejected); return; } setError(''); setQuery(''); setOpen(false); setActive(0); }
  return <div className="find instrument-search"><Button type="button" className="find__plus" aria-label={account + ' ETF 또는 현금 찾기'} onClick={() => { setOpen(!open); document.getElementById(id)?.focus(); }}>+</Button>
    <TextInput id={id} className="field" role="combobox" aria-label={account + '에 담은 ETF 찾기'} aria-autocomplete="list" aria-expanded={open} aria-controls={id + '-list'} aria-activedescendant={open && options[active] ? id + '-' + active : undefined} autoComplete="off" placeholder="ETF 추가하기" value={query} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onChange={e => { setQuery(e.target.value); setError(''); setOpen(true); setActive(0); }} onKeyDown={e => {
      if (e.key === 'Escape') setOpen(false);
      if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(Math.max(0, Math.min(active + 1, options.length - 1))); }
      if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(0, active - 1)); }
      if (e.key === 'Enter' && open && options[active]) { e.preventDefault(); choose(options[active]); }
    }} />
    {error && <p role="alert">{error}</p>}
    {open && <div className="sug" onMouseDown={e => e.preventDefault()}><div className="catalog-note">ETF·현금만 선택할 수 있어요</div><div role="listbox" id={id + '-list'}>{options.map((item, i) => <Button type="button" role="option" aria-selected={active === i} id={id + '-' + i} key={item.id} onClick={() => choose(item)}><span>{item.name}<small>{item.kind === 'cash' ? '원화 현금' : `${item.market === 'KR' ? '국내 상장 ETF' : '미국 상장 ETF'} · ${item.ticker}`}</small></span></Button>)}</div>{!catalog && <p className="catalog-note" role="status">ETF 목록이 준비되지 않았어요. 현금은 먼저 추가할 수 있어요.</p>}{catalog && !options.length && <p className="catalog-note">일치하는 ETF가 없어요. 종목코드나 티커로 다시 찾아 주세요.</p>}{catalog?.counts && <p className="catalog-note">국내 {catalog.counts.KR.toLocaleString()}개 · 미국 {catalog.counts.US.toLocaleString()}개</p>}</div>}
  </div>;
}
