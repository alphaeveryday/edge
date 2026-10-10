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
  groups: WatchGroup[];
  group: string;
  band: Signal;
  // 0~4 범위의 그룹 signal 서수 평균
  // 구판 서버 응답의 필드 부재
  score?: number;
  changePct: number;
  etfs: EtfSummary[];
}

// 커서 목록의 한 페이지
// next 부재 시 마지막 페이지
export interface Page<T> {
  items: T[];
  next: string | null;
}

export interface Post {
  id: string;
  etf: Pick<EtfSummary, 'code' | 'theme' | 'logoBg'> & { short: string };
  author: { name: string; handle: string; avatarBg: string };
  time: string;
  title?: string;
  body: string;
  quoteTag?: string;
  like: number;
  reply: number;
  liked: boolean;
  views?: number;
  mine?: boolean;
  // 내가 차단한 작성자의 글 여부
  // 글 상세 응답에만 포함
  blocked?: boolean;
}

export interface Candle {
  o?: number;
  h?: number;
  l?: number;
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
  dir?: Dir;
}

export interface HoldingRow {
  name: string;
  weight: number;
  dir?: Dir;
  desc?: string;
}

export interface EtfDetailData {
  insight?: { dir: Dir; text: string };
  stocks: HeatCell[];
  themes?: HeatCell[];
  holdings: HoldingRow[];
  themeRows?: HoldingRow[];
  stockCount: number;
  info: { k: string; v: string }[];
  blurb: string;
}

export type VoteChoice = 'buy' | 'wait' | 'sell';

export interface VoteStat {
  code: EtfCode;
  count: number;
  pct: Record<VoteChoice, number>;
  mine: VoteChoice | null;
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
  prevWeek?: string | null;
  nextWeek?: string | null;
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

export interface Me {
  nick: string;
  handle: string;
  avatarBg: string;
  email?: string;
  disclaimerAcceptedAt?: string;
}

export type ReportReason = 'spam' | 'abuse' | 'sexual' | 'scam' | 'etc';

export interface Reply {
  id: string;
  author: { name: string; handle: string; avatarBg: string };
  time: string;
  body: string;
}

export type NotiKind = 'watch' | 'comm';

// 서버 /auth/social 의 provider
export type SocialProvider = 'kakao';

export interface Notification {
  id: string;
  kind: NotiKind;
  etf?: EtfCode;
  postId?: string;
  time: string;
  title: string;
  body: string;
  read: boolean;
}
