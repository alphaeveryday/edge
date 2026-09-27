import type { ApiClient } from '../client';
import type { Signal } from '@/theme/tokens';
import { SIGNAL_ORDER } from '@/theme/tokens';
import { ETFS, GROUPS, GROUP_MEMBERS, POSTS } from './data';

const delay = <T,>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

// 인메모리 쓰기 상태. 앱 재시작 시 초기화
const posts = POSTS.map((p) => ({ ...p }));

const avgSignal = (codes: string[]): Signal => {
  const idx = codes.map((c) => SIGNAL_ORDER.indexOf(ETFS.find((e) => e.code === c)!.signal));
  const m = Math.round(idx.reduce((a, b) => a + b, 0) / Math.max(idx.length, 1));
  return SIGNAL_ORDER[m] ?? 'neutral';
};

export const mockClient: ApiClient = {
  etf: {
    get: (code) => {
      const e = ETFS.find((x) => x.code === code);
      return e ? delay(e) : Promise.reject(new Error(`unknown etf ${code}`));
    },
  },
  home: {
    brief: (group = 'base') => {
      const codes = GROUP_MEMBERS[group] ?? [];
      const etfs = codes.map((c) => ETFS.find((e) => e.code === c)!);
      const changePct = etfs.length ? Math.round((etfs.reduce((a, e) => a + e.changePct, 0) / etfs.length) * 10) / 10 : 0;
      return delay({ asOf: '오늘 08:30 기준', groups: GROUPS, group, band: avgSignal(codes), changePct, etfs });
    },
  },
  community: {
    hot: () => delay(posts.map((p) => ({ ...p }))),
    toggleLike: (id) => {
      const p = posts.find((x) => x.id === id);
      if (!p) return Promise.reject(new Error(`unknown post ${id}`));
      p.liked = !p.liked;
      p.like += p.liked ? 1 : -1;
      return delay({ ...p }, 40);
    },
  },
};
