import type { Signal } from '@/theme/tokens';
import type { Axis, Dir, EtfSummary, Me, VoteStat, VoteChoice, Post, Reply, Theme, ThemeDetail, ThemeFeedItem } from '../types';

// 계약 응답의 앱 타입 변환과 표시용 값 생성

const PALETTE = ['#3D34E0', '#131318', '#0E8A6C', '#E0562B', '#8B34E0', '#E8A13D', '#C9820E', '#1B64DA', '#6C5CF5'];
const hash = (s: string) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = (h * 16777619) >>> 0; } return h; };
export const bgOf = (key: string) => PALETTE[hash(key) % PALETTE.length];

const AXIS_LABEL: Record<string, Axis> = { issue: '이슈', chart: '차트', macro: '매크로', value: '밸류', flow: '수급' };
const AXIS_CODE: Record<Axis, string> = { 이슈: 'issue', 차트: 'chart', 매크로: 'macro', 밸류: 'value', 수급: 'flow' };
export const axisLabel = (code: string): Axis => AXIS_LABEL[code] ?? '이슈';
export const axisCode = (axis: Axis) => AXIS_CODE[axis];

// ISO 시각의 상대 시각 표기
export const ago = (iso: string, now = Date.now()) => {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return '방금';
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
};
export const asOfLabel = (iso: string) => {
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `오늘 ${hh}:${mm} 기준`;
};

const updatedLabel = (iso: string) => { const d = new Date(iso); return `${d.getMonth() + 1}월 ${d.getDate()}일 업데이트`; };

export interface WireEtfSummary { code: string; name: string; theme: string; price: number; changePct: number; signal: Signal; hot?: boolean; sub?: string }
export const etf = (e: WireEtfSummary): EtfSummary => ({ ...e, logoBg: bgOf(e.theme) });

export interface WireTheme { key: string; label: string; group: 'industry' | 'asset'; hot?: boolean }
export const theme = (t: WireTheme): Theme => ({ ...t, bg: bgOf(t.key) });

export interface WireThemeFeedItem { key: string; count: number; headline: string; dir: Dir }
export const themeFeedItem = (t: WireThemeFeedItem, label: string): ThemeFeedItem => ({ ...t, label, bg: bgOf(t.key) });

export type WireThemeDetail = Omit<ThemeDetail, 'stocks' | 'label'> & { stocks: { name: string; etfs: string }[] };
export const themeDetail = (t: WireThemeDetail, label: string): ThemeDetail => ({ ...t, label, updated: updatedLabel(t.updated), stocks: t.stocks.map((s) => ({ ...s, logoBg: bgOf(s.name) })) });

export interface WireMe { nick: string; handle: string; email?: string; disclaimerAcceptedAt?: string }
export const me = (m: WireMe): Me => ({ ...m, avatarBg: bgOf(m.handle) });

export interface WirePost {
  id: string; etf: { code: string; theme: string; short: string }; author: { name: string; handle: string }; time: string;
  title?: string; body: string; quoteTag?: string; repostOf?: { name: string; handle: string; time: string; body: string };
  like: number; reply: number; repost: number; liked: boolean; views?: number; mine?: boolean;
}
export const post = (p: WirePost): Post => ({
  ...p,
  etf: { ...p.etf, logoBg: bgOf(p.etf.theme) },
  author: { ...p.author, avatarBg: bgOf(p.author.handle) },
  time: ago(p.time),
  repostOf: p.repostOf ? { name: p.repostOf.name, avatarBg: bgOf(p.repostOf.handle), time: ago(p.repostOf.time), body: p.repostOf.body } : undefined,
});

export interface WireReply { id: string; author: { name: string; handle: string }; time: string; body: string }
export const reply = (r: WireReply): Reply => ({ id: r.id, author: { name: r.author.name, avatarBg: bgOf(r.author.handle) }, time: ago(r.time), body: r.body });

export interface WireVoteCount { buys: number; waits: number; sells: number; source: string }
// 서버 인원 수 기반 비율과 앱이 기억한 내 선택
export const voteStat = (code: string, c: WireVoteCount, mine: VoteChoice | null): VoteStat => {
  const n = c.buys + c.waits + c.sells;
  const p = (x: number) => (n ? Math.round((x / n) * 100) : 0);
  return { code, count: n, pct: { buy: p(c.buys), wait: p(c.waits), sell: p(c.sells) }, mine };
};

export interface WirePage<T> { items: T[]; nextCursor: string | null }
