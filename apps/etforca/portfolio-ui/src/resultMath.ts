import type { History, Input, Report } from './api';

// 8차 결과 화면의 금액·판정·과거 성과·앞으로 내 돈 계산. 화면 코드(ResultView)와 떼어 따로 검사한다(portfolio-ui/AGENTS.md: 금융 계산은 디자인 코드에 넣지 않는다).
export type Kind = 'keep' | 'one' | 'rebuild' | 'start';
export type Row = { id: string; name: string; account: string; kind: string; before: number | null; target: number | null; weight: number | null };
// 앞으로 내 돈 칸의 빠르기. 제품은 1년 전망만 내므로 화면이 정한 가정이고, 칸에 그대로 적는다(계약의 남은 결정 3).
export const ANNUAL_RATE = .05;
export const toNum = (value: unknown) => {
  const n = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN;
  return Number.isFinite(n) ? n : null;
};
/** 원 금액을 '1억 2,780만 원', '767만 5,920원'처럼 읽는 꼴로. round 면 만 원 아래를 반올림한다. */
export function won(value: number, round = false) {
  let v = Math.round(Math.abs(value));
  if (round && v >= 10000) v = Math.round(v / 10000) * 10000;
  const sign = value < 0 && v ? '-' : '';
  if (v < 10000) return `${sign}${v.toLocaleString('ko-KR')}원`;
  const eok = Math.floor(v / 1e8), man = Math.floor(v % 1e8 / 1e4), rest = v % 1e4;
  const parts = [eok ? `${eok.toLocaleString('ko-KR')}억` : '', man ? `${man.toLocaleString('ko-KR')}만` : '', rest ? rest.toLocaleString('ko-KR') : ''].filter(Boolean);
  return sign + parts.join(' ') + (rest ? '원' : ' 원');
}
export const displayName = (id: string, name?: string) => id.startsWith('US:') ? id.slice(3) : id.startsWith('CASH:') ? '현금' : name || id.split(':').pop() || id;
export function finalRows(report: Report): Row[] {
  const plan = report.recommendation;
  if (!plan) return (report.metrics?.holdings ?? []).map(h => ({ id: h.name, name: h.name, account: h.account, kind: h.name === '현금' ? 'cash' : 'etf', before: null, target: null, weight: h.weight_pct }));
  const allocations = plan.allocations ?? [];
  const find = (id: string, account: string) => allocations.find(a => a.instrument_id === id && a.account === account) ?? allocations.find(a => a.instrument_id === id);
  const execution = plan.execution;
  if (execution?.rows?.length && (execution.status === 'ready' || execution.status === 'conditional')) {
    return execution.rows.map(row => {
      const allocation = find(row.instrument_id, row.account);
      return { id: row.instrument_id, name: displayName(row.instrument_id, row.name || allocation?.name), account: row.account,
        kind: row.instrument_id.startsWith('CASH:') ? 'cash' : allocation?.kind ?? 'etf', before: toNum(row.before_krw), target: toNum(row.target_krw), weight: allocation?.weight_pct ?? null };
    }).filter(row => (row.before ?? 0) > 0 || (row.target ?? 0) > 0);
  }
  return allocations.map(a => ({ id: a.instrument_id, name: displayName(a.instrument_id, a.name), account: a.account, kind: a.kind, before: null, target: toNum(a.amount_krw), weight: Number.isFinite(a.weight_pct) ? a.weight_pct : null }));
}

export function verdictKind(report: Report, rows: Row[], input?: Input): Kind | null {
  if (report.verdict?.kind) return report.verdict.kind;
  if (!report.recommendation) return null;
  const held = input?.holdings ? input.holdings.some(h => h.kind !== 'cash' && !h.instrument_id?.startsWith('CASH:'))
    : (report.metrics?.holdings ?? []).some(h => h.name !== '현금');
  const known = rows.filter(r => r.before !== null && r.target !== null);
  if (known.length ? !known.some(r => r.kind !== 'cash' && r.before! > 0) : !held) return 'start';
  const invested = known.filter(r => r.kind !== 'cash');
  const beforeSum = known.reduce((sum, r) => sum + r.before!, 0), targetSum = known.reduce((sum, r) => sum + r.target!, 0);
  // 밖에서 돈을 같은 비중으로 더 넣는 옛 계획은 '바꾸는' 것이 아니다 — 금액이 아니라 (현금 행을 포함한) 전체 대비 비중으로 센다.
  // 현금으로 사거나 팔아 현금을 남기면 그 종목의 비중이 바뀌므로 바뀐 것으로 센다(서버 규칙: 사고팔 행이 있으면 keep 이 아님).
  const moved = (r: Row) => (r.before! < 1) !== (r.target! < 1) || beforeSum > 0 && targetSum > 0 && Math.abs(r.before! / beforeSum - r.target! / targetSum) > .005;
  const changed = new Set(known.length
    ? invested.filter(moved).map(r => r.id)
    : (report.recommendation.actions ?? []).filter(a => !a.instrument_id.startsWith('CASH:') && (a.before_pct ?? 0) !== a.after_pct).map(a => a.instrument_id));
  return changed.size === 0 ? 'keep' : changed.size === 1 ? 'one' : 'rebuild';
}
// 옛 실행표(8차 전)는 추가 자금·여러 달 납입을 목표 금액에 넣었다. 새 안내(final_note)가 없으면 그 조건을 한 줄로 밝혀
// 목표 금액을 지금 바로 할 거래로 읽지 않게 한다.
export function legacyNote(execution?: NonNullable<Report['recommendation']>['execution']) {
  if (!execution || (execution.status !== 'ready' && execution.status !== 'conditional')) return '';
  // 계좌 사이로 돈을 옮겨야 하는 옛 실행표: 한 계좌에서 판 돈이 다른 계좌의 매수 대금이 되지 않으므로 넣고 뺄 돈을 밝힌다.
  const transfers = (execution.accounts ?? []).flatMap(a => [
    (toNum(a.withdrawal_krw) ?? 0) > 0 ? `${a.account}에서 ${won(toNum(a.withdrawal_krw)!)} 빼기` : '',
    (toNum(a.deposit_krw) ?? 0) > 0 ? `${a.account}에 ${won(toNum(a.deposit_krw)!)} 넣기` : '']).filter(Boolean);
  const move = transfers.length ? `계좌 사이 옮길 돈: ${transfers.join(', ')}.` : '';
  if (execution.status !== 'conditional') return move;
  const additional = toNum(execution.additional_krw) ?? 0, monthly = toNum(execution.monthly_krw), months = execution.months;
  const plan = months && months > 1 && monthly ? `매달 ${won(monthly)}씩 ${months}개월 넣어 맞추는 계획이에요.` : '';
  const extra = additional > 0 ? `추천 금액에는 새로 넣을 ${won(additional)}이 들어 있어요.` : '';
  return [extra, plan, move].filter(Boolean).join(' ') || '추천 금액에는 앞으로 넣을 돈이 들어 있어요.';
}
/** 고점에서 저점까지 가장 많이 줄어든 돈과, 같은 구간의 고점 대비 비율(금액과 비율이 다른 구간에서 오지 않게). */
export function deepestFall(values: number[]) {
  let peak = values[0], amount = 0, ratio = 0;
  for (const v of values) {
    peak = Math.max(peak, v);
    if (peak - v > amount) { amount = peak - v; ratio = peak > 0 ? amount / peak : 0; }
  }
  return { amount, ratio };
}
export function periodLabel(start: string, end: string) {
  const days = Math.max(1, Math.round((Date.parse(end) - Date.parse(start)) / 864e5));
  const years = days / 365.25, months = Math.round(days / 30.44);
  if (years >= .95 && Math.abs(years - Math.round(years)) < .15) return `지난 ${Math.round(years)}년`;
  return months >= 1 ? `지난 ${months}개월` : `지난 ${days}일`;
}
export type Past = { label: string; start: string; end: string; mode: 'compare' | 'cash' | 'single'; total: number; rec: number[] | null; cur: number[] | null;
  recEnd: number; curEnd: number; recLoss: number; curLoss: number; recDrop: number | null; curDrop: number | null; basis: string; singleName: string };
export function pastOf(report: Report, history: History | null | undefined, total: number | null, kind: Kind | null): Past | null {
  if (!total || total <= 0) return null;
  const points = (history?.points ?? []).filter(p => Number.isFinite(p.recommended) && p.recommended > 0 && Number.isFinite(Date.parse(p.date)));
  const server = report.past && ['rec_end_krw', 'cur_end_krw', 'rec_max_loss_krw', 'cur_max_loss_krw'].every(key => toNum(report.past![key as keyof typeof report.past]) !== null) ? report.past : null;
  if (points.length < 2 && !server) return null;
  const start = report.period?.start ?? points[0]?.date ?? '', end = report.period?.end ?? points.at(-1)?.date ?? '';
  const hasCurrent = points.length >= 2 && points.every(p => Number.isFinite(p.current) && p.current! > 0);
  const cash = history?.baseline_kind === 'new_cash' || kind === 'start';
  // 서버 요약(past)이 기록과 다른 기간을 말하면 그 기록으로 그래프를 그리지 않는다 — 제목과 그래프가 다른 숫자를 보이면 안 된다.
  const sameSpan = !server || !report.period || points.length >= 2 && points[0].date === report.period.start && points.at(-1)!.date === report.period.end;
  const rec = points.length >= 2 && sameSpan ? points.map(p => total * p.recommended / points[0].recommended) : null;
  const cur = hasCurrent && !cash ? points.map(p => total * p.current! / points[0].current!) : null;
  const recEnd = server ? toNum(server.rec_end_krw)! : rec!.at(-1)!;
  const curEnd = server ? toNum(server.cur_end_krw)! : cash ? total : cur?.at(-1) ?? recEnd;
  const recFall = rec ? deepestFall(rec) : null, curFall = cur ? deepestFall(cur) : null;
  const recLoss = server ? Math.abs(toNum(server.rec_max_loss_krw)!) : recFall!.amount;
  const curLoss = server ? Math.abs(toNum(server.cur_max_loss_krw)!) : cash ? 0 : curFall ? curFall.amount : recLoss;
  // 서버 요약은 금액만 준다. 같은 기간 기록이 있을 때만 비율을 기록에서 읽고, 없으면 비율을 지어내지 않는다.
  const recDrop = recFall?.ratio ?? null;
  const curDrop = cash ? 0 : curFall?.ratio ?? (cur ? null : recDrop);
  // 바꿀 것이 없으면 두 선이 같다 — 같은 숫자를 두 번 보이지 않고 한 줄로 말한다.
  const same = !cash && (kind === 'keep' || (!server && !cur) || Math.abs(recEnd - curEnd) < 1 && Math.abs(recLoss - curLoss) < 1);
  return { label: report.period?.label || (start && end ? periodLabel(start, end) : '지난 기간'), start, end, mode: cash ? 'cash' : same ? 'single' : 'compare',
    total, rec, cur, recEnd, curEnd, recLoss, curLoss, recDrop, curDrop, basis: history?.basis ?? '',
    // 한 줄로 줄일 때: 지금 구성과 같으면 '지금 그대로', 지금 구성의 기록이 없으면 추천만 보인다.
    singleName: cur || kind === 'keep' ? '지금 그대로' : '추천' };
}
export function futureValue(principal: number, monthly: number, years: number) {
  const month = (1 + ANNUAL_RATE) ** (1 / 12) - 1;
  return principal * (1 + ANNUAL_RATE) ** years + (monthly ? monthly * ((1 + month) ** (12 * years) - 1) / month : 0);
}
