// 리포트 화면의 순수 로직 — 형식·정렬·차트 좌표. 컴포넌트와 분리해 node --test 로 직접 검사한다.

export type FactorDir = 'help' | 'burden' | 'neutral';

export interface Factor {
  dir: FactorDir;
  /** 5요인 축: 이슈·매크로·수급·차트·밸류 */
  ax: string;
  t: string;
  sub: string;
}

export interface FactorGroup {
  head: string;
  items: Factor[];
}

/** [종목명, ETF 등락 기여도(%p)] */
export type Contribution = [string, number];

export interface QueuedReport {
  code: string;
  name: string;
  theme: string;
  /** 전일 종가 대비 등락률(%) */
  pct: number;
  /** 고객 안내 문구 */
  say: string;
  title: string;
  groups: FactorGroup[];
  contrib: Contribution[];
  /** 장 시작부터 종가까지 10분봉(전일 종가 대비 %) — 마지막 봉은 15:30 종가 단일가 */
  bars: Candle[];
}

export interface DemoReport extends QueuedReport {
  /** 화면을 연 시각 기준 몇 분 전에 받았는지 */
  ago: number;
}

export interface FeedItem extends QueuedReport {
  id: string;
  /** 수신 시각(epoch ms, 데모 시계 기준) */
  recv: number;
  read: boolean;
  fresh: boolean;
}

export const MIN = 60_000;
/** 데모 기준 시각 2026-11-10 15:40 KST(장 마감 뒤 — 리포트가 종가 기준이다). 화면을 연 뒤 실제 경과 시간만큼 흐른다. */
export const DEMO_BASE = Date.UTC(2026, 10, 10, 6, 40);
export const DEMO_DATE = '2026-11-10';

export function direction(p: number): 1 | -1 | 0 {
  return p > 0 ? 1 : p < 0 ? -1 : 0;
}

/** 기여도 표기: +0.92%p / −0.07%p */
export function formatPp(p: number): string {
  return (p > 0 ? '+' : p < 0 ? '−' : '') + Math.abs(p).toFixed(2) + '%p';
}

/** epoch ms → KST HH:MM */
export function hhmm(t: number): string {
  const d = new Date(t + 9 * 3600_000);
  return String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0');
}

export function relative(now: number, t: number): string {
  const m = Math.floor((now - t) / MIN);
  return m < 1 ? '방금' : m < 60 ? m + '분 전' : Math.floor(m / 60) + '시간 ' + (m % 60) + '분 전';
}

/**
 * 기여 종목 순서 — ETF 가 움직인 방향으로 기여한 종목을 먼저, 각 묶음 안에서는 기여 크기순.
 * 접힌 상태(TOP 3)는 방향이 같은 종목만 보여 준다: 오른 날 TOP 3 에 하락 기여 종목이 끼면
 * "왜 올랐나"에 대한 답이 흐려진다.
 */
export function orderContributions(pct: number, contrib: Contribution[]) {
  const up = pct >= 0;
  const byMagnitude = (a: Contribution, b: Contribution) => Math.abs(b[1]) - Math.abs(a[1]);
  const lead = contrib.filter((c) => (up ? c[1] > 0 : c[1] < 0)).sort(byMagnitude);
  const rest = contrib.filter((c) => !lead.includes(c)).sort(byMagnitude);
  return { lead, all: [...lead, ...rest] };
}

// ── 업종 아이콘 (ETF Orca 앱 SectorIcon 이식) — 테마 해시 배경색 + 업종 글리프 ──
const PALETTE = ['#3D34E0', '#131318', '#0E8A6C', '#E0562B', '#8B34E0', '#E8A13D', '#C9820E', '#1B64DA', '#6C5CF5'];

function fnv(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h;
}

export function themeColor(theme: string): string {
  return PALETTE[fnv(theme) % PALETTE.length];
}

export type SectorKind = 'chip' | 'power' | 'defense' | 'bio' | 'green' | 'machine' | 'etc';

export function sectorKind(theme: string): SectorKind {
  if (/반도체|AI|IT|소프트|로봇|테크/.test(theme)) return 'chip';
  if (/전력|원자력/.test(theme)) return 'power';
  if (/방산|국방/.test(theme)) return 'defense';
  if (/바이오|헬스|제약/.test(theme)) return 'bio';
  if (/친환경|신재생|2차전지|에너지전환/.test(theme)) return 'green';
  if (/기계|산업재/.test(theme)) return 'machine';
  return 'etc';
}

// ── 10분봉 차트 ──
export interface Candle {
  o: number;
  c: number;
  h: number;
  l: number;
}
