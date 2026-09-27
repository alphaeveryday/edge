import type { ApiClient } from '../client';
import type { WatchGroup } from '../types';
import type { Signal } from '@/theme/tokens';
import { SIGNAL_ORDER } from '@/theme/tokens';
import { ETFS, GROUP_MEMBERS, GROUPS, POSTS, THEMES } from './data';

const delay = <T,>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

// 인메모리 쓰기 상태. 앱 재시작 시 초기화
const posts = POSTS.map((p) => ({ ...p }));
const groups = GROUPS.map((g) => ({ ...g }));
const members: Record<string, string[]> = Object.fromEntries(Object.entries(GROUP_MEMBERS).map(([k, v]) => [k, [...v]]));
let recent = ['AXAI', 'DEFN', 'GRID'];

const etfOf = (code: string) => ETFS.find((e) => e.code === code)!;
const groupList = (): WatchGroup[] => groups.map((g) => ({ ...g, count: (members[g.key] ?? []).length }));

const avgSignal = (codes: string[]): Signal => {
  const idx = codes.map((c) => SIGNAL_ORDER.indexOf(etfOf(c).signal));
  const m = Math.round(idx.reduce((a, b) => a + b, 0) / Math.max(idx.length, 1));
  return SIGNAL_ORDER[m] ?? 'neutral';
};

export const mockClient: ApiClient = {
  etf: {
    get: (code) => {
      const e = ETFS.find((x) => x.code === code);
      if (!e) return Promise.reject(new Error(`unknown etf ${code}`));
      recent = [code, ...recent.filter((c) => c !== code)].slice(0, 5);
      return delay(e);
    },
    list: () => delay(ETFS),
    search: (q) => {
      const k = q.trim();
      return delay(k ? ETFS.filter((e) => e.name.includes(k) || e.theme.includes(k) || (e.sub ?? '').includes(k)) : [], 80);
    },
    recent: () => delay(recent.map(etfOf)),
  },
  watch: {
    groups: () => delay(groupList()),
    list: (group) => delay((members[group] ?? []).map(etfOf)),
    createGroup: (label) => {
      const g = { key: 'g' + Date.now(), label };
      groups.push(g);
      members[g.key] = [];
      return delay({ ...g, count: 0 });
    },
    deleteGroup: (key) => {
      if (key === 'base') return Promise.reject(new Error('기본 관심은 지울 수 없어요'));
      const i = groups.findIndex((g) => g.key === key);
      if (i >= 0) groups.splice(i, 1);
      delete members[key];
      return delay(undefined);
    },
    setMembers: (group, codes) => {
      members[group] = [...codes];
      return delay(undefined, 40);
    },
    membership: (code) => delay(groups.filter((g) => (members[g.key] ?? []).includes(code)).map((g) => g.key)),
    setMembership: (code, keys) => {
      for (const g of groups) {
        const cur = members[g.key] ?? [];
        const want = keys.includes(g.key);
        if (want && !cur.includes(code)) members[g.key] = [...cur, code];
        if (!want && cur.includes(code)) members[g.key] = cur.filter((c) => c !== code);
      }
      return delay(undefined, 40);
    },
  },
  theme: {
    list: () => delay(THEMES),
  },
  onboarding: {
    complete: ({ etfs }) => {
      members.base = [...etfs];
      return delay(undefined);
    },
  },
  home: {
    brief: (group = 'base') => {
      const codes = members[group] ?? [];
      const etfs = codes.map(etfOf);
      const changePct = etfs.length ? Math.round((etfs.reduce((a, e) => a + e.changePct, 0) / etfs.length) * 10) / 10 : 0;
      return delay({ asOf: '오늘 08:30 기준', groups: groupList(), group, band: avgSignal(codes), changePct, etfs });
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
