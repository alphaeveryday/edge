import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Button, TextArea, TextInput } from './design-system/components';
import type { Evaluation, Input, Report, Section } from './api';
import { safeReportUrl } from './reportUrl';
import { trackResult } from './analytics';
import { AppDownloadSheet } from './AppDownloadSheet';
import { ChatScreen, useConversation, waiting } from './Followup';
import { TypeCardSheet, typeCards } from './TypeCard';
import orca from '../../app-ui/assets/icon.png';
import './result.css';
import { ANNUAL_RATE, displayName, finalRows, futureValue, legacyNote, pastOf, toNum, verdictKind, won, type Kind, type Past, type Row } from './resultMath';

// 8차 결과: 누구에게나 같은 일곱 칸(답 → 최종 추천 → 앞으로 넣을 돈 → 지난 기간 → 앞으로 내 돈 → 오르카의 분석 → 더 물어보기)과
// 그 뒤의 유형 카드 줄·설문 카드. 서버가 새 필드(verdict·answer·past …)를 아직 안 낸 옛 보고서는 기존 필드로 같은 칸을 채운다.

type Item = { title: string; paragraphs: string[]; evidence: Section['evidence']; limitations: string[] };

const axes = [['leadership', '시장 주도성'], ['profitability', '수익성'], ['stability', '안정성']] as const;
const grades = ['심각', '개선 필요', '보통', '훌륭', '매우 훌륭'];
const gradeIndex = (value: number) => value >= 4.5 ? 4 : value >= 3.5 ? 3 : value >= 2.5 ? 2 : value >= 1.5 ? 1 : 0;
const palette = ['--chart-1', '--chart-ocean', '--chart-violet', '--chart-3', '--chart-teal', '--chart-coral', '--chart-4', '--chart-6'].map(name => `var(${name})`);
const constraintLabels: Record<string, string> = { no_leverage: '레버리지 빼기', some_bonds: '채권도 조금', dividend: '배당 받기', no_sell: '갖고 있는 건 팔지 않기', kr_listed_etf_only: '국내 상장 ETF만' };

const short = (value: number) => won(value, true).replace(/ ?원$/, '');
const pct = (value: number) => value > 0 && value < .1 ? '0.1% 미만' : `${Number(value.toFixed(1))}%`;
const score = (value: number) => value.toFixed(1);
const clean = (text: string) => text.replace(/^#+\s*/gm, '').replace(/\*\*/g, '').trim();
// 표 조각('| a | b |')이나 머리표만 남은 줄은 고객에게 보일 문장이 아니다.
const sentence = (text: string) => { const t = clean(text); return t && (t.match(/\|/g)?.length ?? 0) < 2 && !/^[-:|\s]+$/.test(t) ? t : ''; };


const headlines: Record<Kind, [string, string]> = { keep: ['', '이대로 가도 돼요'], one: ['하나만', ' 바꾸면 돼요'], rebuild: ['다시 짜야 해요', ''], start: ['', '이렇게 시작하세요'] };
function Headline({ report, kind }: { report: Report; kind: Kind | null }) {
  const tone = kind === 'one' ? 'warn' : kind === 'rebuild' ? 'danger' : '';
  // 서버 한 줄은 어느 낱말을 칠할지 모르므로 한 줄 전체를 판정 색으로 칠한다. 옛 보고서는 시안의 기본 한 줄과 강조를 쓴다.
  if (report.verdict?.headline) return <h1 className="r8-headline"><span data-tone={tone || undefined}>{report.verdict.headline}</span></h1>;
  if (!kind) return <h1 className="r8-headline">이번에는 추천을 만들지 못했어요</h1>;
  const [strong, rest] = headlines[kind];
  return <h1 className="r8-headline">{strong && <span data-tone={tone}>{strong}</span>}{rest}</h1>;
}

const starPath = 'M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z';
function Stars({ value, size = 24 }: { value: number; size?: number }) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '');
  return <span className="r8-stars" role="img" aria-label={`별 5개 중 ${score(value)}개`}>{Array.from({ length: 5 }, (_, i) => {
    const fill = `${Math.max(0, Math.min(1, value - i)) * 100}%`;
    return <svg key={i} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id={`${id}s${i}`}><stop offset={fill} style={{ stopColor: 'var(--color-star)' }} /><stop offset={fill} style={{ stopColor: 'var(--color-border)' }} /></linearGradient></defs><path d={starPath} fill={`url(#${id}s${i})`} /></svg>;
  })}</span>;
}
const Chevron = ({ open = false }: { open?: boolean }) => <svg className="r8-chevron" data-open={open || undefined} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>;
const Arrow = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>;

function avatarText(row: Row) {
  if (row.kind === 'cash') return '현금';
  if (row.id.startsWith('US:')) return row.id.slice(3, 8);
  return row.name.match(/^([A-Z]{2,6})\s/)?.[1] ?? row.name.slice(0, 2);
}
function Avatar({ row, color }: { row: Row; color: string }) {
  const text = avatarText(row);
  return <span className="r8-avatar" data-kind={row.kind === 'stock' ? 'stock' : undefined} data-long={text.length >= 5 || undefined} aria-hidden="true">{text}<i style={{ background: color }} /></span>;
}

function groupBy<T>(items: T[], key: (item: T) => string) {
  const groups = new Map<string, T[]>();
  for (const item of items) groups.set(key(item), [...groups.get(key(item)) ?? [], item]);
  return [...groups];
}

function Change({ row }: { row: Row }) {
  const { before, target } = row;
  if (target === null) return row.weight !== null ? <span className="r8-change"><span>{pct(row.weight)}</span></span> : null;
  if (before === null) return row.weight !== null ? <span className="r8-change"><span>{pct(row.weight)}</span></span> : null;
  if (Math.abs(target - before) < 1) return <span className="r8-change"><span className="r8-strong-2">{won(before)}</span><span>그대로</span></span>;
  const cash = row.kind === 'cash';
  const tone = cash ? undefined : target > before ? 'buy' : 'sell';
  const text = cash ? target < before ? `${won(before - target)} 써요` : `${won(target - before)} 남겨요`
    : before < 1 ? '새로 사기' : target < 1 ? '전부 팔기' : target > before ? `${won(target - before)} 더 사기` : `${won(before - target)} 팔기`;
  return <span className="r8-change"><span>{won(before)}</span><span className="r8-arrow">→</span><b data-tone={tone}>{won(target)}</b><span data-tone={tone}>{text}</span></span>;
}

function FinalCard({ report, rows, color, input }: { report: Report; rows: Row[]; color: (id: string) => string; input?: Input }) {
  const plan = report.recommendation;
  const composition = groupBy(rows.filter(r => (r.target ?? r.weight ?? 0) > 0), r => r.id).map(([id, items]) => {
    const total = rows.reduce((sum, r) => sum + (r.target ?? 0), 0);
    const amount = items.reduce((sum, r) => sum + (r.target ?? 0), 0);
    const weight = total > 0 ? amount / total * 100 : items.reduce((sum, r) => sum + (r.weight ?? 0), 0);
    return { id, name: items[0].name.replace(/^[A-Z]{2,6}\s+/, ''), weight };
  }).filter(c => c.weight > 0);
  const total = rows.every(r => r.target !== null) && rows.length ? rows.reduce((sum, r) => sum + r.target!, 0) : toNum(plan?.total_krw ?? report.metrics?.total_krw);
  const maxPositions = (input as { constraints?: { kind: string; value?: number }[] } | undefined)?.constraints?.find(c => c.kind === 'max_positions')?.value;
  const note = report.final_note || (!plan ? withheld(report) : legacyNote(plan.execution));
  return <section className="r8-card r8-final" data-analytics-section="final" aria-labelledby="r8-final-title">
    <div className="r8-row-between"><h2 id="r8-final-title" className="r8-title">최종 추천 포트폴리오</h2>
      {Number.isFinite(plan?.evaluation?.overall) && <span className="r8-rating"><svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d={starPath} /></svg><b>{score(plan!.evaluation.overall!)}</b><span>/ 5</span></span>}</div>
    {!!report.constraint_results?.length && <div className="r8-badges">{report.constraint_results.map(c => <span key={c.kind} className="r8-badge" data-tone={c.status === 'kept' ? 'success' : 'warning'} title={c.note || undefined}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{c.status === 'kept' ? <path d="M20 6 9 17l-5-5" /> : <path d="M12 8v5m0 3h.01" />}</svg>
      {c.kind === 'max_positions' ? `종목 ${maxPositions ?? 'n'}개 이하` : constraintLabels[c.kind] ?? c.kind} · {c.status === 'kept' ? '지켰어요' : '대신했어요'}</span>)}</div>}
    {composition.length > 0 && <div className="r8-alloc">
      <div className="r8-bar" role="img" aria-label={`비중 ${composition.map(c => `${c.name} ${pct(c.weight)}`).join(', ')}`}>{composition.map(c => <span key={c.id} style={{ flex: `${c.weight} 1 0`, background: color(c.id) }} />)}</div>
      <div className="r8-legend">{composition.map(c => <span key={c.id}><i style={{ background: color(c.id) }} />{c.name}<b>{pct(c.weight)}</b></span>)}</div>
    </div>}
    {groupBy(rows, r => r.account).map(([account, items]) => <div className="r8-group" key={account}>
      <span className="r8-badge">{account}</span>
      <ul className="r8-list">{items.map(row => {
        const gone = row.target !== null && row.target < 1 && (row.before ?? 0) > 0;
        return <li className="r8-final-row" key={row.id + row.account} data-gone={gone || undefined}><Avatar row={row} color={color(row.id)} />
          <span className="r8-row-main"><span className="r8-row-name" title={row.name}>{row.name}</span><Change row={row} /></span>
          <span className="r8-row-value">{row.target !== null ? won(row.target) : row.weight !== null ? pct(row.weight) : ''}</span></li>;
      })}</ul>
    </div>)}
    {total !== null && total > 0 && <div className="r8-total"><span>합계</span><b>{won(total)}</b></div>}
    {note && <p className="r8-callout" role="status"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v5m0-8h.01" /></svg><span>{note}</span></p>}
  </section>;
}


function withheld(report: Report) {
  const reasons: Record<string, string> = {
    CUSTOMER_CONSTRAINT_NOT_SATISFIED: '고르신 조건을 모두 지키는 구성을 찾지 못했어요.',
    EXECUTION_INFEASIBLE: '지금 계좌와 금액으로 옮길 방법을 찾지 못했어요.',
    RATING_BELOW_MINIMUM: '추천 기준에 맞는 구성을 찾지 못했어요.',
    BASELINE_PRICE_UNAVAILABLE: '가진 종목의 가격 기록을 받지 못해 견주지 못했어요.',
    NO_COMMON_PRICE_HISTORY: '견줄 수 있는 같은 기간의 가격 기록이 없었어요.',
  };
  return `${reasons[report.optimization?.reason || ''] ?? '이번 분석에서는 추천 구성을 만들지 못했어요.'} 지금 가진 구성을 그대로 보여 드려요.`;
}

function MonthlyCard({ report, rows, color, monthly, onAlert }: { report: Report; rows: Row[]; color: (id: string) => string; monthly: number | null; onAlert: () => void }) {
  const plan = report.recommendation;
  if (!plan) return null;
  const nameOf = (id: string) => rows.find(r => r.id === id)?.name ?? displayName(id, plan.allocations.find(a => a.instrument_id === id)?.name);
  const kindOf = (id: string) => rows.find(r => r.id === id)?.kind ?? plan.allocations.find(a => a.instrument_id === id)?.kind ?? 'etf';
  const planned = (plan.execution?.monthly_plan ?? []).map(m => ({ id: m.instrument_id, account: m.account, amount: toNum(m.krw) })).filter(m => (m.amount ?? 0) > 0);
  // 서버 배분(split_monthly)처럼 개별 주식에는 매달 더 넣지 않는다.
  const invested = plan.allocations.filter(a => a.kind !== 'cash' && a.kind !== 'stock' && a.weight_pct > 0);
  const share = invested.reduce((sum, a) => sum + a.weight_pct, 0);
  const lines = planned.length ? planned.map(m => ({ ...m, value: won(m.amount!) }))
    : invested.map(a => ({ id: a.instrument_id, account: a.account, amount: null, value: pct(a.weight_pct / share * 100) }));
  if (!lines.length) return null;
  const sum = planned.reduce((total, m) => total + m.amount!, 0);
  const title = planned.length ? `매달 ${won(sum)}은 여기에` : monthly ? `매달 ${won(monthly)}은 이 비율로` : '넣을 돈이 생기면 이 비율로';
  return <section className="r8-card" data-analytics-section="monthly" aria-labelledby="r8-monthly-title">
    <h2 id="r8-monthly-title" className="r8-title">{title}</h2>
    {groupBy(lines, l => l.account).map(([account, items]) => <div className="r8-group" key={account}>
      <span className="r8-badge">{account}</span>
      <ul className="r8-list">{items.map(l => <li className="r8-list-row" key={l.id + l.account}><Avatar row={{ id: l.id, name: nameOf(l.id), account, kind: kindOf(l.id), before: null, target: null, weight: null }} color={color(l.id)} /><span className="r8-row-main"><span className="r8-row-name">{nameOf(l.id)}</span></span><span className="r8-row-value">{l.value}</span></li>)}</ul>
    </div>)}
    {(monthly ?? sum) > 0 && <Button className="r8-app" onClick={onAlert}>
      <span className="r8-app-icon"><img src={orca} width="32" height="32" alt="" /><i aria-hidden="true">1</i></span>
      <span className="r8-app-text"><b>매달 넣는 날 알림 받기</b><span>그날 넣을 종목과 금액을 알려 드려요</span></span>
      <span className="r8-app-free">무료<Arrow /></span>
    </Button>}
  </section>;
}


function PastChart({ past }: { past: Past }) {
  const rec = past.rec!, cur = past.mode === 'compare' ? past.cur : null;
  const L = 2, R = 254, T = 14, B = 144;
  const values = [...rec, ...(cur ?? []), past.total];
  const lo = Math.min(...values), hi = Math.max(...values), pad = (hi - lo) * .08 || hi * .05 || 1;
  const y = (v: number) => B - (v - lo + pad) / (hi - lo + 2 * pad) * (B - T);
  const sample = (series: number[]) => {
    const n = series.length, count = Math.min(n, 60);
    return Array.from({ length: count }, (_, k) => { const i = Math.round(k * (n - 1) / (count - 1)); return [L + i / (n - 1) * (R - L), y(series[i])] as const; });
  };
  const line = (series: number[]) => sample(series).map(([x, v]) => `${x.toFixed(1)},${v.toFixed(1)}`).join(' ');
  const recEndY = y(rec.at(-1)!), otherY = cur ? y(cur.at(-1)!) : y(past.total);
  // 끝에 단 두 이름표가 겹치지 않게 위아래로 벌린다.
  let [a, b] = [recEndY, otherY];
  if (Math.abs(a - b) < 36) { const mid = (a + b) / 2, up = a <= b ? -18 : 18; a = mid + up; b = mid - up; }
  [a, b] = [a, b].map(v => Math.max(16, Math.min(150, v)));
  const otherName = past.mode === 'cash' ? '현금' : '지금 그대로';
  return <svg className="r8-past-chart" viewBox="0 0 350 168" width="100%" role="img" aria-label={`${past.label} 그래프: 추천 ${won(past.recEnd, true)}${past.mode === 'single' ? '' : `, ${otherName} ${won(past.curEnd, true)}`}`}>
    <line className="r8-base" x1={L} x2={R} y1={y(past.total)} y2={y(past.total)} />
    <text className="r8-axis" x={L} y={y(past.total) - 6}>넣은 돈 {won(past.total, true)}</text>
    {cur && <><polyline className="r8-line-cur" points={line(cur)} /><circle className="r8-dot-cur" cx={R} cy={otherY} r="4" /></>}
    <polygon className="r8-area" points={`${line(rec)} ${R},${B} ${L},${B}`} />
    <polyline className="r8-line-rec" points={line(rec)} />
    <circle className="r8-dot-rec" cx={R} cy={recEndY} r="5.5" />
    <text className="r8-tag-rec" x="266" y={a - 6}>추천</text><text className="r8-val-rec" x="266" y={a + 10}>{short(past.recEnd)}</text>
    {past.mode !== 'single' && <><text className="r8-tag" x="266" y={b - 6}>{otherName}</text><text className="r8-val" x="266" y={b + 10}>{short(past.curEnd)}</text></>}
    <text className="r8-axis" x={L} y="164">그때</text><text className="r8-axis" x={R} y="164" textAnchor="end">지금</text>
  </svg>;
}

function PastCard({ past, unavailable }: { past: Past | null; unavailable: string }) {
  if (!past) return unavailable ? <section className="r8-card" data-analytics-section="past"><span className="r8-eyebrow">지난 기간</span><p className="r8-muted">{unavailable}</p></section> : null;
  const diff = past.recEnd - past.curEnd;
  const gain = past.recEnd - past.total;
  const heading = past.mode === 'cash' ? <>현금보다 <em>{won(Math.abs(diff), true)}</em> {diff >= 0 ? '더 벌었어요' : '덜 벌었어요'}</>
    : past.mode === 'single' ? <><em>{won(Math.abs(gain), true)}</em> {gain >= 0 ? '벌었어요' : '잃었어요'}</>
    : diff >= 0 ? <><em>{won(diff, true)}</em> 더 벌었어요</> : <>지금 구성이 <em>{won(-diff, true)}</em> 더 벌었어요</>;
  const otherName = past.mode === 'cash' ? '현금' : '지금 그대로';
  const boxes = past.mode === 'single' ? [[past.singleName, past.recLoss, past.recDrop] as const] : [[otherName, past.curLoss, past.curDrop] as const, ['추천', past.recLoss, past.recDrop] as const];
  const worse = past.mode === 'single' || Math.abs(past.recLoss - past.curLoss) < 1 ? -1 : past.curLoss > past.recLoss ? 0 : 1;
  const lossDiff = past.curLoss - past.recLoss;
  return <section className="r8-card r8-past" data-analytics-section="past" aria-labelledby="r8-past-title">
    <div className="r8-head"><span className="r8-eyebrow">{past.label}, 이렇게 했다면</span><h2 id="r8-past-title" className="r8-big">{heading}</h2></div>
    {past.rec && <PastChart past={past} />}
    <div className="r8-loss"><span className="r8-eyebrow">가장 크게 떨어졌을 때 내 돈</span>
      <div className="r8-loss-boxes">{boxes.map(([name, loss, drop], i) => <div key={name} className="r8-loss-box" data-worse={i === worse || undefined}><span>{name}</span><b>{won(-loss, true)}</b>{drop !== null && <span>{(-drop * 100).toFixed(1)}%</span>}</div>)}</div>
      {past.mode === 'compare' && Math.abs(lossDiff) >= 1 && <p className="r8-strong">추천이 {lossDiff > 0 ? <><em>{won(lossDiff, true)}</em> 덜 잃었어요</> : <>{won(-lossDiff, true)} 더 잃었어요</>}</p>}
      {past.mode === 'cash' && <p className="r8-muted">현금은 떨어지지 않지만 그동안 늘지도 않았어요.</p>}
    </div>
    {past.start && past.end && <span className="r8-foot">{past.start.replaceAll('-', '.')} ~ {past.end.replaceAll('-', '.')}{past.basis.includes('재투자') ? ' · 분배금은 다시 투자' : ''} · 세금과 매매 비용은 넣지 않았어요</span>}
  </section>;
}

function FutureCard({ principal, monthly, past }: { principal: number | null; monthly: number; past: Past | null }) {
  if (principal === null || principal < 0 || principal === 0 && monthly <= 0) return null;
  const bars = [5, 10, 15].map(years => ({ years, value: futureValue(principal, monthly, years), paid: principal + monthly * 12 * years }));
  const final = bars[2].value;
  const rate = ANNUAL_RATE * 100;
  const drop = past?.recDrop ?? 0; // 비율을 모르면 나쁜 해 줄을 숨긴다.
  return <section className="r8-card r8-future" data-analytics-section="future" aria-labelledby="r8-future-title">
    <div className="r8-head"><span className="r8-eyebrow">앞으로 내 돈</span>
      <h2 id="r8-future-title" className="r8-big">15년 뒤 <em>{won(final, true)}</em></h2>
      <span className="r8-sub">{monthly > 0 ? `지금 ${won(principal)} + 매달 ${won(monthly)}이면, 연 ${rate}%면` : `지금 ${won(principal)}을 이대로 두면, 연 ${rate}%면`}</span></div>
    <div className="r8-bars" role="img" aria-label={bars.map(b => `${b.years}년 뒤 ${won(b.value, true)}, 넣은 돈 ${won(b.paid, true)}`).join('. ')}>{bars.map((b, i) => <div key={b.years} className="r8-bar-col" data-final={i === 2 || undefined}>
      <b>{short(b.value)}</b>
      <span className="r8-stack" style={{ height: `${Math.max(24, b.value / final * 150)}px` }}><span className="r8-grown" style={{ flex: `${Math.max(0, b.value - b.paid)} 1 0` }} /><span className="r8-paid" style={{ flex: `${b.paid} 1 0` }} /></span>
      <span>{b.years}년 뒤</span></div>)}</div>
    <div className="r8-legend"><span><i className="r8-paid" />넣은 돈</span><span><i className="r8-grown" />불어난 돈</span></div>
    {drop > .001 && past && <div className="r8-bad"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m22 17-8.5-8.5-5 5L2 7" /><path d="M16 17h6v-6" /></svg>
      <span>{past.label}처럼 -{(drop * 100).toFixed(1)}% 떨어지는 해가 오면</span><b>{won(final * (1 - drop), true)}</b></div>}
    <span className="r8-foot">연 {rate}%로 불어난다고 놓고 계산했어요. 세금과 보수는 넣지 않았어요.</span>
  </section>;
}

function analysisItems(report: Report, kind: Kind | null): Item[] {
  const section = (s?: Section) => ({ paragraphs: (s?.paragraphs ?? []).map(sentence).filter(Boolean), evidence: (s?.evidence ?? []).filter(e => sentence(e.quote)), limitations: (s?.limitations ?? []).map(sentence).filter(Boolean) });
  if (report.analysis?.length) return report.analysis.map(a => ({ title: clean(a.title), ...section(a) })).filter(a => a.paragraphs.length);
  const current = report.current_evaluation, plan = report.recommendation, s = report.sections ?? ({} as Report['sections']);
  const reason = current?.reason?.paragraphs?.length ? current.reason : s.portfolio;
  const items: Item[] = [
    { title: Number.isFinite(current?.overall) && kind !== 'start' ? `왜 ${score(current!.overall!)}점이에요?` : '지금 구성은 어때요?', ...section(reason) },
    { title: kind === 'start' ? '왜 이렇게 골랐어요?' : kind === 'keep' ? '왜 그대로 둬도 돼요?' : '왜 이렇게 바꿔요?', ...section(plan?.evaluation?.reason) },
    { title: '지금 무엇부터 하면 돼요?', ...section(s.opinion) },
    { title: '앞으로는 어떻게 될까요?', ...section(s.outlook) },
    // 겹침·분산 같은 구조 통찰은 결과에 두지 않는다(계획 '문구'). 옛 보고서의 그런 제목은 걸러 낸다.
    ...(report.insights ?? []).filter(i => !/겹|중복|분산|집중/.test(i.title)).map(i => ({ title: clean(i.title), ...section(i.content) })),
  ];
  return items.filter(item => item.paragraphs.length);
}

function ScoreLines({ current, recommended }: { current?: Evaluation; recommended?: Evaluation }) {
  const lines = axes.map(([key, label]) => [label, current?.scores?.[key], recommended?.scores?.[key]] as const)
    .filter(([, c, r]) => Number.isFinite(c) || Number.isFinite(r));
  if (!lines.length) return null;
  return <div className="r8-score-lines">{lines.map(([label, c, r]) => {
    const both = Number.isFinite(c) && Number.isFinite(r);
    const only = (Number.isFinite(c) ? c : r)!;
    return <div className="r8-score-line" key={label}><span>{label}</span><Stars value={both ? c! : only} size={14} />
      <span className="r8-score-nums" aria-label={both ? `지금 ${score(c!)}점, 추천 ${score(r!)}점` : `${score(only)}점`}>{both ? <><span>{score(c!)}</span><Arrow /><b>{score(r!)}</b></> : <b>{score(only)}</b>}</span></div>;
  })}</div>;
}

function Evidence({ item, report, analysisId }: { item: Item; report: Report; analysisId: string }) {
  const [open, setOpen] = useState(false);
  if (!item.evidence.length && !item.limitations.length) return null;
  return <details className="r8-evidence" onToggle={e => { const next = e.currentTarget.open; setOpen(next); if (next) trackResult('evidence_opened', { analysis_id: analysisId }); }}>
    <summary>{item.evidence.length ? `근거 ${item.evidence.length}개` : '확인한 범위'}<Chevron open={open} /></summary>
    {item.evidence.map((e, i) => { const href = safeReportUrl(report.sources?.find(s => s.id === e.source_id)?.url); return <blockquote key={i}>{sentence(e.quote)}{href && <a href={href} target="_blank" rel="noreferrer">원문 보기 ↗</a>}</blockquote>; })}
    {item.limitations.map((text, i) => <p key={i}>{text}</p>)}
  </details>;
}

function AnalysisCard({ report, kind, analysisId }: { report: Report; kind: Kind | null; analysisId: string }) {
  const items = analysisItems(report, kind);
  if (!items.length) return null;
  const sources = report.sources?.length ?? 0;
  return <section className="r8-card r8-analysis" data-analytics-section="analysis" aria-labelledby="r8-analysis-title">
    <div className="r8-orca-head"><span className="r8-orca"><img src={orca} width="40" height="40" alt="" /></span><div><h2 id="r8-analysis-title" className="r8-title">오르카의 분석</h2>{sources > 0 && <span className="r8-sub">자료 {sources}개를 읽고 정리했어요</span>}</div></div>
    {items.map((item, i) => <article className="r8-item" key={i}>
      <div className="r8-item-head"><span className="r8-num">{i + 1}</span><h3>{item.title}</h3></div>
      {i === 0 && <ScoreLines current={kind === 'start' ? undefined : report.current_evaluation} recommended={report.recommendation?.evaluation} />}
      <ul className="r8-dots">{item.paragraphs.map((p, j) => <li key={j}>{p}</li>)}</ul>
      {i === 0 && report.current_evaluation?.rubric_version === 1 && <p className="r8-foot">점수는 확인한 자료로만 매겨요. 적어 주신 선호는 점수에 넣지 않아요.</p>}
      <Evidence item={item} report={report} analysisId={analysisId} />
    </article>)}
  </section>;
}

const defaultQuestions: Record<Kind, string[]> = {
  keep: ['매달 넣는 돈을 늘리면?', '조금 더 공격적으로 가면?', '채권을 조금 넣으면?', '언제 다시 점검하면 돼요?'],
  one: ['하나도 안 바꾸면?', '조금 더 공격적으로 가면?', '세금은 얼마나 나와요?', '언제 다시 점검하면 돼요?'],
  rebuild: ['절반만 바꾸면?', '세금은 얼마나 나와요?', '조금 더 안정적으로 가면?', '언제 다시 점검하면 돼요?'],
  start: ['매달 넣는 돈을 줄이면?', '계좌는 어디서 열어요?', '조금 더 안정적으로 가면?', '하나만 산다면?'],
};
function AskCard({ jobId, questions }: { jobId: string; questions: string[] }) {
  const chat = useConversation(jobId, true);
  const [draft, setDraft] = useState('');
  const answering = chat.items.some(waiting);
  const blocked = chat.sending || answering || chat.remaining === 0;
  function ask(question: string, suggested: boolean) {
    if (!question.trim() || blocked) return;
    if (suggested) trackResult('ask_suggested_clicked', { analysis_id: jobId });
    chat.open();
    void chat.send(question).then(sent => { if (sent && !suggested) setDraft(''); });
  }
  function submit(event: FormEvent) { event.preventDefault(); ask(draft, false); }
  return <section className="r8-card r8-ask" data-analytics-section="ask" aria-labelledby="r8-ask-title">
    <div className="r8-orca-head"><span className="r8-orca"><img src={orca} width="40" height="40" alt="" /></span><div><h2 id="r8-ask-title" className="r8-title">더 궁금한 게 있나요?</h2><span className="r8-sub">이어서 물어볼 만한 것</span></div>
      <span className="r8-badge" data-tone="primary">{chat.remaining}번 남음</span></div>
    <div className="r8-ask-rows">{questions.slice(0, 4).map(q => <Button key={q} className="r8-ask-row" disabled={blocked} onClick={() => ask(q, true)}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /></svg>
      <span>{q}</span><Arrow /></Button>)}</div>
    <form className="r8-ask-input" onSubmit={submit}><TextInput aria-label="직접 물어보기" placeholder="직접 물어보기" maxLength={1000} value={draft} disabled={blocked} onChange={e => setDraft(e.target.value)} />
      <Button variant="primary" aria-label="질문 보내기" disabled={blocked || !draft.trim()}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" /></svg></Button></form>
    {chat.items.length > 0 && <Button className="r8-ask-resume" onClick={chat.open}>이 분석에서 나눈 대화 보기 · {chat.items.length}개{answering ? ' · 답을 쓰는 중' : ''}</Button>}
    {chat.remaining === 0 ? <p className="r8-muted">이 분석의 질문 {chat.limit}번을 모두 썼어요. 새 분석에서 다시 물을 수 있어요.</p>
      : answering ? <p className="r8-muted">앞선 질문의 답을 쓰고 있어요. 답이 끝나면 이어서 물을 수 있어요.</p> : null}
    {chat.error && !chat.isOpen && <p role="alert" className="r8-error">{chat.error}</p>}
    <ChatScreen chat={chat} />
  </section>;
}

const surveySteps = {
  fit: { title: '이번 결과는 내 투자 목표와 보유 상황을 얼마나 반영했나요?', choices: [['none', '전혀 반영하지 않았어요'], ['low', '별로 반영하지 않았어요'], ['some', '어느 정도 반영했어요'], ['enough', '충분히 반영했어요'], ['unsure', '판단하기 어려워요']] },
  worry: { title: '결과를 보기 전과 비교해, 내 포트폴리오에 대한 고민은 어떻게 달라졌나요?', choices: [['much_less', '많이 줄었어요'], ['less', '조금 줄었어요'], ['same', '그대로예요'], ['more', '늘었어요'], ['unsure', '판단하기 어려워요']] },
  worry_reason: { title: '어떤 점 때문에 고민이 늘었나요?', choices: [['unknown_issue', '몰랐던 문제를 알게 됐어요'], ['mismatch', '추천이 내 상황과 안 맞아요'], ['next_step', '무엇부터 할지 모르겠어요']] },
} as const;
type SurveyStep = keyof typeof surveySteps | 'done';
const surveyShown = new Set<string>();
// 결과 맨 끝 카드. 한 번에 한 문항, 분석 하나에 한 번(남은 문항은 같은 브라우저에서 이어서). 팝업으로 띄우지 않는다.
function Survey({ jobId }: { jobId: string }) {
  const key = `edge-portfolio-survey:${jobId}`;
  const [step, setStep] = useState<SurveyStep>(() => {
    try { const saved = localStorage.getItem(key); return saved === 'done' || saved && Object.hasOwn(surveySteps, saved) ? saved as SurveyStep : 'fit'; } catch { return 'fit'; }
  });
  const [finished] = useState(step === 'done');
  const [reason, setReason] = useState('');
  const [text, setText] = useState('');
  const card = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  useEffect(() => {
    if (finished || surveyShown.has(jobId) || !card.current) return;
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(e => e.isIntersecting) || surveyShown.has(jobId)) return;
      surveyShown.add(jobId); trackResult('survey_shown', { analysis_id: jobId }); observer.disconnect();
    }, { threshold: .5 });
    observer.observe(card.current);
    return () => observer.disconnect();
  }, [jobId, finished]);
  useEffect(() => { if (moved.current) heading.current?.focus({ preventScroll: true }); }, [step]);
  function go(next: SurveyStep) {
    moved.current = true; setStep(next);
    try { localStorage.setItem(key, next); } catch { /* 이 탭 안에서는 이어서 답할 수 있다. */ }
  }
  function answer(question: 'fit' | 'worry', value: string) {
    trackResult('survey_answered', { analysis_id: jobId, question, answer: value });
    go(question === 'fit' ? 'worry' : value === 'more' ? 'worry_reason' : 'done');
  }
  function sendReason() {
    if (reason || text.trim()) trackResult('survey_answered', { analysis_id: jobId, question: 'worry_reason', answer: reason || 'custom', ...(text.trim() ? { text: text.trim().slice(0, 500) } : {}) });
    go('done');
  }
  if (finished) return null;
  const label = step === 'fit' ? '결과를 더 낫게 만드는 질문 1/2' : step === 'worry' ? '결과를 더 낫게 만드는 질문 2/2' : step === 'worry_reason' ? '하나만 더 · 선택' : '';
  return <section ref={card} className="r8-card r8-survey" data-analytics-section="survey" aria-label="결과 설문">
    {step === 'done' ? <div className="r8-survey-done" role="status"><h3 ref={heading} tabIndex={-1}>답해 주셔서 고마워요</h3><p>다음 분석을 고치는 데 그대로 써요</p></div> : <>
      <div className="r8-row-between"><span className="r8-eyebrow">{label}</span>{step !== 'worry_reason' && <span className="r8-steps" aria-hidden="true"><i data-on={step === 'fit' || undefined} /><i data-on={step === 'worry' || undefined} /></span>}</div>
      <h3 ref={heading} tabIndex={-1}>{surveySteps[step].title}</h3>
      <div className="r8-choices">{surveySteps[step].choices.map(([value, text]) => step === 'worry_reason'
        ? <Button key={value} className="r8-choice" aria-pressed={reason === value} onClick={() => setReason(reason === value ? '' : value)}>{text}</Button>
        : <Button key={value} className="r8-choice" data-muted={value === 'unsure' || undefined} onClick={() => answer(step, value)}>{text}</Button>)}</div>
      {step === 'worry_reason' && <><TextArea className="r8-survey-text" aria-label="직접 적기 (선택)" placeholder="직접 적기 (선택)" rows={2} maxLength={500} value={text} onChange={e => setText(e.target.value)} />
        <div className="r8-survey-actions"><Button className="r8-tertiary" onClick={() => go('done')}>건너뛰기</Button><Button variant="primary" onClick={sendReason}>보내기</Button></div></>}
    </>}
  </section>;
}

// 끝내지 못한 분석(시안 F1). 실패한 분석은 하루 횟수에서 빠진다(store.submit 의 state!='failed').
export function FailedView({ failure, partial, canRetry, busy, onRetry, onEdit, children }: { failure: string | null; partial?: Report; canRetry: boolean; busy: boolean; onRetry: () => void; onEdit: () => void; children?: ReactNode }) {
  const reason = failure === 'SEARCH_RATE_LIMIT' ? '자료 조회가 잠시 지연됐어요.' : failure === 'TIMED_OUT' ? '분석이 예상보다 오래 걸려 멈췄어요.' : '분석을 마무리하다가 멈췄어요.';
  const overall = partial?.current_evaluation?.overall;
  return <div className="r8 r8-failed">
    <div className="r8-state"><img src={orca} width="112" height="112" alt="" />
      <h1>이번 분석을<br />끝내지 못했어요</h1>
      <p>{reason}<br />입력한 내용은 그대로 있어요. 같은 입력으로 바로 다시 할 수 있어요.</p>
      <span className="r8-badge" data-tone="success"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>횟수는 줄지 않았어요</span>
    </div>
    {children && <details className="r8-card r8-partial"><summary><b>멈추기 전까지 본 것</b>{Number.isFinite(overall) && <span>지금 구성 {score(overall!)}점 · {grades[gradeIndex(overall!)]}</span>}<Chevron /></summary>{children}</details>}
    <div className="r8-state-actions">{canRetry && <Button variant="primary" loading={busy} onClick={onRetry}>같은 입력으로 다시 분석</Button>}<Button className="r8-tertiary" disabled={busy} onClick={onEdit}>입력 고치기</Button></div>
  </div>;
}

const shown = new Set<string>();
export function ResultView({ report, jobId, input, active = true }: { report: Report; jobId: string; input?: Input; active?: boolean }) {
  const [appOpen, setAppOpen] = useState(false);
  const [typeOpen, setTypeOpen] = useState(false);
  const rows = finalRows(report);
  // 추천 객체만 있고 행·점수·글이 모두 비면 빈 결과다 — 노출로 세지 않는다.
  const hasContent = Number.isFinite(report.current_evaluation?.overall) || Number.isFinite(report.recommendation?.evaluation?.overall) || rows.length > 0 || analysisItems(report, null).length > 0;
  useEffect(() => {
    // 실제 결과 칸이 그려졌고 고객이 그 탭을 보고 있을 때 분석 하나에 한 번(빈 결과·뒤쪽 탭·StrictMode 재실행은 세지 않는다).
    const key = `edge-portfolio-result-shown:${jobId}`;
    const seen = () => { try { return shown.has(jobId) || localStorage.getItem(key) === '1'; } catch { return shown.has(jobId); } };
    if (!hasContent || !active || seen()) return;
    function send() {
      if (document.visibilityState !== 'visible' || seen()) return;
      shown.add(jobId); trackResult('result_shown', { analysis_id: jobId });
      try { localStorage.setItem(key, '1'); } catch { /* 새로 고침 뒤 다시 셀 수 있지만 화면은 막지 않는다. */ }
    }
    send();
    document.addEventListener('visibilitychange', send);
    return () => document.removeEventListener('visibilitychange', send);
  }, [jobId, hasContent, active]);
  const colors = new Map<string, string>();
  for (const row of [...rows, ...(report.recommendation?.allocations ?? []).map(a => ({ id: a.instrument_id, kind: a.kind }))]) {
    if (!colors.has(row.id)) colors.set(row.id, row.kind === 'cash' ? 'var(--chart-slate)' : palette[colors.size % palette.length]);
  }
  const color = (id: string) => colors.get(id) ?? 'var(--chart-slate)';
  const kind = verdictKind(report, rows, input);
  const plan = report.recommendation;
  const execution = plan?.execution;
  const monthly = toNum(execution?.monthly_krw) ?? toNum((input as { monthly_krw?: number } | undefined)?.monthly_krw);
  const currentTotal = rows.some(r => r.before !== null) ? rows.reduce((sum, r) => sum + (r.before ?? 0), 0) : toNum(report.metrics?.total_krw ?? plan?.total_krw);
  const finalTotal = rows.length && rows.every(r => r.target !== null) ? rows.reduce((sum, r) => sum + r.target!, 0) : toNum(plan?.total_krw ?? report.metrics?.total_krw);
  const past = plan ? pastOf(report, plan.historical_performance, currentTotal || finalTotal, kind) : null;
  const holdingsCount = new Set(rows.filter(r => r.kind !== 'cash' && (r.before ?? (plan ? 0 : 1)) > 0).map(r => r.id)).size
    || (input?.holdings ?? []).filter(h => h.kind !== 'cash').length;
  const date = report.as_of ? new Date(report.as_of) : null;
  const meta = [date && Number.isFinite(date.getTime()) ? `${date.getMonth() + 1}월 ${date.getDate()}일 기준` : '',
    kind === 'start' ? '처음 시작' : holdingsCount ? `종목 ${holdingsCount}개` : '', currentTotal ? won(currentTotal) : '', monthly ? `매달 ${won(monthly)}` : ''].filter(Boolean).join(' · ');
  const answer = clean(report.answer || report.completion?.tasks?.find(t => t.answer)?.answer || report.sections?.question?.paragraphs?.map(sentence).find(Boolean) || '');
  const question = input?.question?.trim() || '이대로 가도 될까요?';
  const current = report.current_evaluation?.overall;
  const type = report.type_card && typeCards[report.type_card.key] ? report.type_card : null;
  const questions = report.suggested_questions?.filter(q => q.trim()).length ? report.suggested_questions.filter(q => q.trim()) : defaultQuestions[kind ?? 'keep'];
  return <div className="r8" id="result">
    <section className="r8-card r8-hero" data-analytics-section="answer" aria-label="물어보신 것의 답">
      {meta && <span className="r8-meta">{meta}</span>}
      {kind !== 'start' && Number.isFinite(current) ? <>
        <div className="r8-score"><span className="r8-score-label">지금 내 포트폴리오</span><span className="r8-score-value"><b>{score(current!)}</b><span>/ 5</span></span><Stars value={current!} /></div>
        <ol className="r8-grades" aria-label={`등급 ${grades[gradeIndex(current!)]}`}>{grades.map((g, i) => <li key={g} data-on={i === gradeIndex(current!) || undefined}><i /><span>{g}</span></li>)}</ol>
      </> : <div className="r8-hero-orca"><img src={orca} width="120" height="120" alt="" /></div>}
      <div className="r8-hero-answer">
        <div className="r8-quote"><span>물어보신 것</span><q>{question}</q></div>
        <Headline report={report} kind={kind} />
        {answer && <p className="r8-answer">{answer}</p>}
      </div>
    </section>
    <FinalCard report={report} rows={rows} color={color} input={input} />
    <MonthlyCard report={report} rows={rows} color={color} monthly={monthly} onAlert={() => { trackResult('app_alert_clicked', { analysis_id: jobId }); setAppOpen(true); }} />
    {plan && <PastCard past={past} unavailable={plan.historical_performance ? '' : sentence(plan.unavailable_reason || '')} />}
    {plan && <FutureCard principal={currentTotal ?? finalTotal} monthly={monthly ?? 0} past={past} />}
    <AnalysisCard report={report} kind={kind} analysisId={jobId} />
    <AskCard jobId={jobId} questions={questions} />
    {type && <section className="r8-card r8-actions" aria-label="결과 뒤에 할 일">
      <Button className="r8-action-row" onClick={() => setTypeOpen(true)}><span className="r8-icon-tile" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="3" width="16" height="18" rx="3" /><path d="M8 8h8M8 12h5" /></svg></span>
        <span className="r8-row-main"><b>내 투자 유형 카드 만들기</b><span>금액은 빼고 비중만 담아요</span></span><Arrow /></Button>
    </section>}
    {hasContent && <Survey jobId={jobId} />}
    <AppDownloadSheet open={appOpen} onClose={() => setAppOpen(false)} onStoreClick={() => trackResult('store_link_clicked', { analysis_id: jobId })} />
    {type && <TypeCardSheet open={typeOpen} card={type} analysisId={jobId} onClose={() => setTypeOpen(false)} />}
  </div>;
}
