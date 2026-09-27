import type { Signal } from '@/theme/tokens';

export type EtfCode = string;

export interface EtfSummary {
  code: EtfCode;
  name: string;
  theme: string;
  logoBg: string;
  price: number;
  changePct: number;
  signal: Signal;
  hot?: boolean;
  sub?: string;
}

export interface Theme {
  key: string;
  label: string;
  group: 'industry' | 'asset';
  bg: string;
  hot?: boolean;
}

export interface WatchGroup {
  key: string;
  label: string;
  count: number;
}

export interface HomeBrief {
  asOf: string;
  groups: WatchGroup[];
  group: string;
  band: Signal;
  changePct: number;
  etfs: EtfSummary[];
}

export interface Post {
  id: string;
  etf: Pick<EtfSummary, 'code' | 'theme' | 'logoBg'> & { short: string };
  author: { name: string; handle: string; avatarBg: string };
  time: string;
  title?: string;
  body: string;
  quoteTag?: string;
  repostOf?: { name: string; avatarBg: string; time: string; body: string };
  like: number;
  reply: number;
  repost: number;
  liked: boolean;
}

export interface Candle {
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface ChartData {
  range: string;
  candles: Candle[];
  ma5: (number | null)[];
  ma20: (number | null)[];
  axis: string[];
}

export interface MoveInfo {
  ago: string;
  text: string;
  foot: string;
  sheetTitle: string;
  groups: { head: string; items: { dir: Dir; t: string; sub?: string }[] }[];
}

export interface HeatCell {
  name: string;
  weight: number;
  changePct: number;
  dir: Dir;
}

export interface HoldingRow {
  name: string;
  weight: number;
  dir: Dir;
  desc: string;
}

export interface EtfDetailData {
  insight: { dir: Dir; text: string };
  stocks: HeatCell[];
  themes: HeatCell[];
  holdings: HoldingRow[];
  themeRows: HoldingRow[];
  stockCount: number;
  info: { k: string; v: string }[];
  blurb: string;
}

export type PollChoice = 'buy' | 'wait' | 'sell';

export interface Poll {
  code: EtfCode;
  count: number;
  pct: Record<PollChoice, number>;
  mine: PollChoice | null;
}


export type Axis = '이슈' | '차트' | '매크로' | '밸류' | '수급';
export type Dir = 'help' | 'neutral' | 'burden';

export interface AxisRead {
  axis: Axis;
  dir: Dir;
  summary: string;
  hasPage: boolean;
}

export interface DailyDate {
  key: string;
  w: string;
  d: string;
  hasDaily: boolean;
}

export interface DailyAnalysis {
  date: string;
  dates: DailyDate[];
  headTitle: string;
  question: string;
  dateline: string;
  now: Signal;
  prev?: Signal;
  synth: string;
  axes: AxisRead[];
  title: string;
  todayDate: string;
  today: string[];
  args: { no: string; claim: string; body: string[] }[];
  closingTitle: string;
  neg: string[];
  pos: string[];
  close: string;
  next?: { code: EtfCode; name: string };
}

export interface FactorPage {
  axis: Axis;
  dir: Dir;
  headline: string;
  events?: { dir: Dir; k: string; body: string }[];
  chartInds?: { dir: Dir; name: string; d: string }[];
  judg?: string[];
}

export interface MetricTile {
  label: string;
  value: string;
  dir?: Dir;
  note?: string;
  wide?: boolean;
}

export interface MetricPage {
  axis: Axis;
  dir: Dir;
  verdict: string;
  tiles: MetricTile[];
  hasDetail: boolean;
}

export interface Hint {
  title: string;
  orig?: string;
  body: string;
  list?: { k: string; d: string }[];
  why?: string;
}

export interface RankRow {
  etf: EtfSummary;
  rank: number;
  title: string;
  chips: string[];
  ready: boolean;
}

export interface ThemeSheet {
  theme: string;
  title: string;
  why: string;
  rows: { etf: EtfSummary; tag?: string }[];
}

export interface ThemeFeedItem {
  key: string;
  bg: string;
  count: number;
  headline: string;
  dir: Dir;
}

export interface ThemeDetail {
  key: string;
  headline: string;
  stocks: { name: string; logoBg: string; etfs: string }[];
  intro: string;
  updated: string;
  countLabel: string;
  todayLine: string;
  todayEffect: string;
  importantLead: string;
  importantWhy: string;
  metric: { name: string; now: string; dir: Dir; vals: number[]; thresh: number; xLabels: string[]; refLabel: string; state: string };
  thesis: string;
  surface: string;
  structure: string;
  structureWhy: string;
  soWhat: string;
}
