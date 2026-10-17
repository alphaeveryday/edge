export type Asset = { id: string; name: string; account: string; category: string; currency: 'KRW' | 'USD'; quantity: number | null; average: number | null; price: number | null; cost_fx: number | null; amount: number | null; weight: number | null; target: number | null; fee: number; price_date: string | null };
export type Dividend = { id: string; asset_id: string; date: string; status: '실수령' | '예상'; amount: number };
export type Month = { month: string; value: number; deposit: number; withdrawal: number; return_pct: number | null };
export type Workbook = { title: string; fx: number | null; assets: Asset[]; dividends: Dividend[]; months: Month[] };
export type Saved = { version: number; workbook: Workbook | null };
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
