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
  body: string;
  like: number;
  reply: number;
  repost: number;
  liked: boolean;
}
