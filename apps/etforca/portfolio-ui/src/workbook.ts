export type Asset = { id: string; instrument_id?: string | null; name: string; account: string; category: string; currency: 'KRW' | 'USD'; quantity: number | null; quantity_revision?: string | null; average_revision?: string | null; quantity_as_of?: string | null; average_as_of?: string | null; average: number | null; price: number | null; cost_fx: number | null; amount: number | null; weight: number | null; target: number | null; fee: number; price_date: string | null };
export type Dividend = { id: string; asset_id: string; date: string; status: '실수령' | '예상'; amount: number };
export type Month = { month: string; value: number; deposit: number; withdrawal: number; return_pct: number | null };
export type Workbook = { title: string; fx: number | null; assets: Asset[]; dividends: Dividend[]; months: Month[] };
export type Saved = { version: number; workbook: Workbook | null };
export type Quote = { id: string; value: number | null; profit: number | null; price: number | null; price_date: string | null; quantity: number | null; average?: number | null; estimated_quantity?: boolean; dividend_yield_pct?: number | null; annual_dividend_per_share?: number | null; dividend_cycle?: string };
export type Forecast = { asset_id: string; month: string; amount: number };
export function annualDividend(asset: Asset, quote: Quote | undefined): number | null {
  if (asset.category === '현금' || asset.quantity === 0 || asset.quantity === null && asset.amount === 0) return 0;
  return quote?.annual_dividend_per_share != null && quote.value != null && quote.price != null && quote.price > 0 ? quote.annual_dividend_per_share * (quote.value / quote.price) : null;
}
export function dividendMonthCovered(asset: Asset, quote: Quote | undefined, month: string, start: string | null) {
  if (asset.category === '현금' || asset.quantity === 0 || asset.quantity === null && asset.amount === 0) return true;
  const observed = quote?.price_date;
  if (!observed || !start || month < start) return false;
  const last = `${Number(observed.slice(0, 4)) + 1}${observed.slice(4, 7)}`;
  const monthEnd = Number(observed.slice(8)) === new Date(Date.UTC(Number(observed.slice(0, 4)), Number(observed.slice(5, 7)), 0)).getUTCDate();
  return month < last || month === last && monthEnd;
}
export type Tracking = { market: { version: number; payload: { date: string; assets: Quote[]; fx: number | null; forecasts: Forecast[]; missing: string[]; dividend_missing: string[]; pending_splits?: string[] } } | null; days: { date: string; value: number; assets: Quote[]; fx: number | null }[]; archives?: { version: number; payload: Workbook }[] };
export function needsSeed(w: Workbook) { return w.assets.some(a => a.amount === null && a.quantity === null && a.weight !== null); }
export function canApplySeed(w: Workbook) { return needsSeed(w) && w.assets.every(a => a.quantity === null && a.amount === null && a.weight !== null) && Math.abs(w.assets.reduce((sum, a) => sum + (a.weight ?? 0), 0) - 100) <= .01; }
export function withSeed(w: Workbook, seed: number): Workbook {
  const total = w.assets.reduce((s, a) => s + (a.weight ?? 0), 0);
  if (!Number.isFinite(seed) || seed <= 0 || seed > 1e13 || Math.abs(total - 100) > .01 || w.assets.some(a => a.weight === null)) throw new Error('총 투자금액과 보유 비중을 확인해 주세요.');
  let allocated = 0;
  const cents = Math.round(seed * 100);
  return { ...w, assets: w.assets.map((a, i) => { const amount = i === w.assets.length - 1 ? cents - allocated : Math.floor(cents * a.weight! / total); allocated += amount; return { ...a, amount: amount / 100, weight: null }; }) };
}
export const accounts = ['일반 계좌', 'ISA', '연금저축', 'IRP'];
export const money = (n: number | null) => n === null ? '—' : Math.round(n).toLocaleString('ko-KR') + '원';
export function value(a: Asset, fx: number | null): number | null {
  if (a.quantity !== null && a.price !== null) return a.currency === 'USD' && fx === null ? null : a.quantity * a.price * (a.currency === 'USD' ? fx! : 1);
  return a.amount;
}
export function cost(a: Asset): number | null {
  if (a.category === '현금' || a.quantity === null || a.average === null || (a.currency === 'USD' && a.cost_fx === null)) return null;
  return a.quantity * a.average * (a.currency === 'USD' ? a.cost_fx! : 1) + a.fee;
}
export function purchases(assets: Asset[], fx: number | null, budget: number) {
  const rows = assets.filter(a => a.category !== '현금');
  if (!Number.isFinite(budget) || budget < 0 || assets.some(a => value(a, fx) === null)) return [];
  const total = assets.reduce((s, a) => s + value(a, fx)!, 0);
  const targets = assets.reduce((s, a) => s + (a.target ?? 0), 0);
  if (!targets || targets > 100.001) return [];
  const gaps = rows.map(a => Math.max(0, (total + budget) * (a.target ?? 0) / 100 - value(a, fx)!));
  // Unassigned target percentage stays cash; overweight positions are never sold by this buy-only plan.
  const cash = assets.filter(a => a.category === '현금');
  const cashTarget = Math.max(0, 100 - targets) + cash.reduce((s, a) => s + (a.target ?? 0), 0);
  const cashGap = Math.max(0, (total + budget) * cashTarget / 100 - cash.reduce((s, a) => s + value(a, fx)!, 0));
  const gap = Math.max(budget, gaps.reduce((s, n) => s + n, 0) + cashGap);
  return rows.map((a, i) => {
    const allocation = gap ? budget * gaps[i] / gap : 0;
    // Remove binary floating-point tails before testing whole-share affordability.
    const unit = a.price === null || a.currency === 'USD' && fx === null ? null : Number((a.price * (a.currency === 'USD' ? fx! : 1)).toPrecision(15));
    const quantity = unit === 0 ? 0 : unit !== null ? Math.floor(Number((Math.floor(allocation) / unit).toPrecision(15))) : null;
    return { asset: a, quantity, amount: quantity !== null ? Math.ceil(Number((quantity * unit!).toPrecision(15))) : Math.floor(allocation) };
  });
}
export function performance(months: Month[]) {
  let cumulative: number | null = null;
  let previous: Month | undefined;
  return [...months].sort((a, b) => a.month.localeCompare(b.month)).map(m => {
    const consecutive = previous && new Date(`${m.month}-01`).getTime() === Date.UTC(Number(previous.month.slice(0, 4)), Number(previous.month.slice(5)), 1);
    if (!consecutive) cumulative = null;
    const monthly = m.return_pct !== null ? m.return_pct / 100 : consecutive && previous!.value > 0 && !m.deposit && !m.withdrawal ? m.value / previous!.value - 1 : null;
    // Monthly totals cannot locate cash flows in time; accept the broker's return instead of inventing a TWR.
    cumulative = monthly === null ? null : (cumulative ?? 1) * (1 + monthly);
    previous = m;
    return { ...m, monthly, cumulative: cumulative === null ? null : (cumulative - 1) * 100 };
  });
}
