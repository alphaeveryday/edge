import { Button, TextInput } from './design-system/components';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { Catalog, Instrument } from './api';
import { trackAction } from './analytics';

// 한글을 자판에서 치는 낱자로 푼다('삼' → ㅅㅏㅁ, '젅' → ㅈㅓㄴㅈ). 조합 중인 입력칸 값('삼ㅅ', '현댗')도 낱자로는 완성된 이름의 앞부분이다.
const LEAD = 'ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ', VOWEL = 'ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ', TAIL = ' ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ';
const SPLIT: Record<string, string> = { ㅘ: 'ㅗㅏ', ㅙ: 'ㅗㅐ', ㅚ: 'ㅗㅣ', ㅝ: 'ㅜㅓ', ㅞ: 'ㅜㅔ', ㅟ: 'ㅜㅣ', ㅢ: 'ㅡㅣ', ㄳ: 'ㄱㅅ', ㄵ: 'ㄴㅈ', ㄶ: 'ㄴㅎ', ㄺ: 'ㄹㄱ', ㄻ: 'ㄹㅁ', ㄼ: 'ㄹㅂ', ㄽ: 'ㄹㅅ', ㄾ: 'ㄹㅌ', ㄿ: 'ㄹㅍ', ㅀ: 'ㄹㅎ', ㅄ: 'ㅂㅅ' };
const split = (jamo: string) => SPLIT[jamo] ?? jamo.trim();
function keystrokes(text: string) {
  return [...text].map(ch => {
    const c = ch.charCodeAt(0) - 0xac00;
    return c >= 0 && c < 11172 ? LEAD[Math.floor(c / 588)] + split(VOWEL[Math.floor(c % 588 / 28)]) + split(TAIL[c % 28]) : split(ch);
  }).join('');
}
const norm = (text: string) => text.toLocaleUpperCase().replace(/\s/g, '');
const keyCache = new WeakMap<Instrument, { name: string; all: string }>();
const keysOf = (r: Instrument) => {
  let k = keyCache.get(r);
  if (!k) { const name = keystrokes(norm(r.name)); keyCache.set(r, k = { name, all: name + keystrokes(norm(r.ticker + r.code + (r.symbol ?? ''))) }); }
  return k;
};
// 이름·티커·코드 어디에 들어 있어도 찾는다. 순서는 정확히 같은 것 → 이름이 그 글자로 시작하는 것 → 나머지.
// '삼성전자'를 치면 이름에 삼성전자가 든 ETF 16개보다 삼성전자 주식이 먼저 보여야 한다(8개만 보여 준다).
// 낱자로 맞춰서 다 치기 전에도 결과가 비지 않는다 — 예전에는 'ㅅ'·'삼ㅅ'에서 0건이 깜빡여 검색 0건 이벤트 절반이 바로 담기로 이어졌다.
export function matchInstruments(rows: Instrument[], query: string, limit = 20) {
  const q = norm(query), qk = keystrokes(q);
  const rank = (r: Instrument) => {
    const name = norm(r.name);
    if ([name, r.ticker, r.code, r.symbol].some(value => value != null && norm(value) === q)) return 0;
    if (name.startsWith(q) || norm(r.ticker).startsWith(q)) return 1;
    if (norm(r.name + r.ticker + r.code + (r.symbol ?? '')).includes(q)) return 2;
    return keysOf(r).name.startsWith(qk) ? 3 : 4;  // 낱자로만 맞는 것(조합 중이거나 '삼'에 '사마…')은 글자로 맞는 것 뒤
  };
  return rows.filter(r => keysOf(r).all.includes(qk))
    .map(r => ({ r, rank: rank(r) })).sort((a, b) => a.rank - b.rank).map(({ r }) => r).slice(0, limit);
}
export const cashInstrument: Instrument = { id: 'CASH:KRW', market: 'CASH', ticker: 'KRW', code: 'KRW', name: '현금', kind: 'cash' };
export function InstrumentSearch({ account, catalog, onAdd, onOpen, includeStocks = false }: { account: string; catalog: Catalog | null; onAdd: (item: Instrument) => string | void; onOpen?: () => void; includeStocks?: boolean }) {
  const label = includeStocks ? '종목' : 'ETF';
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const id = useId();
  const options = useMemo(() => {
    const q = query.toLocaleUpperCase().replace(/\s/g, '');
    const rows = [cashInstrument, ...((catalog?.instruments || []).filter(i => i.id !== cashInstrument.id && (includeStocks || i.kind !== 'stock')))];
    if (!q) return rows.filter(r => ['CASH:KRW', 'KR:069500', 'KR:360750', 'US:VOO', 'US:QQQM'].includes(r.id));
    return matchInstruments(rows, query);
  }, [query, catalog, includeStocks]);
  const empty = !!catalog && !!query.trim() && !options.length;
  // Counts searches that found nothing (e.g. individual stocks) without sending what was typed.
  // 검색 한 번(열고 고르거나 닫을 때까지)에 한 번만 센다 — 한글 조합 중에는 결과 0건이 깜빡여 같은 검색이 여러 번 잡혔다.
  const emptyReported = useRef(false);
  useEffect(() => { if (empty && !emptyReported.current) { emptyReported.current = true; trackAction('instrument_search_empty'); } }, [empty, query]); // 닫은 뒤 0건 그대로 이어 쳐도 새 검색으로 센다
  useEffect(() => {
    const option = open ? document.getElementById(id + '-' + active) : null;
    const list = option?.closest<HTMLElement>('.sug');
    if (!option || !list) return;
    const row = option.getBoundingClientRect(), bounds = list.getBoundingClientRect();
    if (row.top < bounds.top) list.scrollTop -= bounds.top - row.top;
    else if (row.bottom > bounds.bottom) list.scrollTop += row.bottom - bounds.bottom;
  }, [active, open, id]);
  function choose(item: Instrument) { onOpen?.(); emptyReported.current = false; const rejected = onAdd(item); if (rejected) { setError(rejected); return; } setError(''); setQuery(''); setOpen(false); setActive(0); }
  return <div className="find instrument-search"><Button type="button" className="find__plus" aria-label={account + ` ${label} 또는 현금 찾기`} onClick={() => { setOpen(!open); document.getElementById(id)?.focus(); }}>+</Button>
    <TextInput id={id} className="field" role="combobox" aria-label={account + `에 담은 ${label} 찾기`} aria-autocomplete="list" aria-expanded={open} aria-controls={id + '-list'} aria-activedescendant={open && options[active] ? id + '-' + active : undefined} autoComplete="off" placeholder={label + ' 추가하기'} value={query} onFocus={() => { setOpen(true); onOpen?.(); }} onBlur={() => { setOpen(false); emptyReported.current = false; }} onChange={e => { onOpen?.(); setQuery(e.target.value); setError(''); setOpen(true); setActive(0); }} onKeyDown={e => {
      if (e.key === 'Escape') { setOpen(false); emptyReported.current = false; }
      if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive(Math.max(0, Math.min(active + 1, options.length - 1))); }
      if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(0, active - 1)); }
      if (e.key === 'Enter' && open && options[active]) { e.preventDefault(); choose(options[active]); }
    }} />
    {error && <p role="alert">{error}</p>}
    {open && <div className="sug" onMouseDown={e => e.preventDefault()}><div className="catalog-note">{includeStocks ? '국내·미국 주식, ETF, 현금을 담을 수 있어요' : 'ETF·현금만 선택할 수 있어요'}</div><div role="listbox" id={id + '-list'}>{options.map((item, i) => <Button type="button" role="option" aria-selected={active === i} id={id + '-' + i} key={item.id} onClick={() => choose(item)}><span>{item.name}<small>{item.kind === 'cash' ? '원화 현금' : `${item.market === 'KR' ? '국내' : '미국'} 상장 ${item.kind === 'stock' ? '주식' : 'ETF'} · ${item.ticker}`}</small></span></Button>)}</div>{!catalog && <p className="catalog-note" role="status">{label} 목록이 준비되지 않았어요. 현금은 먼저 추가할 수 있어요.</p>}{empty && <p className="catalog-note">{includeStocks ? '찾는 종목이 없어요. 종목코드나 영문 티커로 다시 찾아 주세요.' : '찾는 ETF가 없어요. 개별 주식은 아직 분석하지 않으니 빼고 진행해도 돼요. ETF는 종목코드나 티커로 다시 찾아 주세요.'}</p>}{catalog?.counts && <p className="catalog-note">국내 {catalog.counts.KR.toLocaleString()}개 · 미국 {catalog.counts.US.toLocaleString()}개</p>}</div>}
  </div>;
}
