import type { ApiClient } from '../client';
import type { Poll, PollChoice, WatchGroup } from '../types';
import type { Signal } from '@/theme/tokens';
import { SIGNAL_ORDER } from '@/theme/tokens';
import { dailyOf, FACTORS, HINTS, METRICS } from './analysis';
import { chartOf, detailOf, ETF_POSTS, moveOf } from './detail';
import { ETFS, GROUP_MEMBERS, GROUPS, POSTS, THEMES } from './data';

const delay = <T,>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

// 인메모리 쓰기 상태. 앱 재시작 시 초기화
const posts = [...POSTS, ...ETF_POSTS].map((p) => ({ ...p }));
const votes: Record<string, PollChoice | null> = {};
const groups = GROUPS.map((g) => ({ ...g }));
const members: Record<string, string[]> = Object.fromEntries(Object.entries(GROUP_MEMBERS).map(([k, v]) => [k, [...v]]));
let recent = ['AXAI', 'DEFN', 'GRID'];

const etfOf = (code: string) => ETFS.find((e) => e.code === code)!;
const groupList = (): WatchGroup[] => groups.map((g) => ({ ...g, count: (members[g.key] ?? []).length }));

const hash = (str: string) => { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = (h * 16777619) >>> 0; } return h; };
// 투표 분포: 종목 코드 시드 + 내 표 1
const pollOf = (code: string): Poll => {
  const h = hash(code);
  const buy = 30 + (h % 23), sell = 18 + ((h >> 5) % 17);
  const base = [buy, 100 - buy - sell, sell].map((n) => Math.max(6, n));
  const n0 = 180 + (h % 900);
  const mine = votes[code] ?? null;
  const keys: PollChoice[] = ['buy', 'wait', 'sell'];
  const counts = base.map((p, i) => Math.round((p * n0) / 100) + (mine === keys[i] ? 1 : 0));
  const n = counts.reduce((a, b) => a + b, 0);
  const pct = counts.map((c) => Math.round((c / n) * 100));
  return { code, count: n, pct: { buy: pct[0], wait: pct[1], sell: pct[2] }, mine };
};

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
    chart: (code, range) => { const e = etfOf(code); return delay(chartOf(code, e.price, e.changePct, range)); },
    move: (code) => delay(moveOf(code, etfOf(code).name)),
    detail: (code) => delay(detailOf(code, etfOf(code).name)),
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
  analysis: {
    daily: (code) => delay(dailyOf(code, etfOf(code).name)),
    factor: (code, axis) => {
      const f = FACTORS[code]?.[axis];
      return f ? delay(f) : Promise.reject(new Error(`no factor page ${code} ${axis}`));
    },
    metric: (code, axis) => {
      const m = METRICS[code]?.[axis];
      return m ? delay(m) : Promise.reject(new Error(`no metric page ${code} ${axis}`));
    },
    hint: (key) => {
      const h = HINTS[key];
      return h ? delay(h, 40) : Promise.reject(new Error(`no hint ${key}`));
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
    hot: () => delay(posts.filter((p) => ['p1', 'p2', 'p3'].includes(p.id)).map((p) => ({ ...p }))),
    posts: (code) => delay(posts.filter((p) => p.etf.code === code && !p.id.startsWith('p')).map((p) => ({ ...p }))),
    poll: (code) => delay(pollOf(code)),
    vote: (code, choice) => {
      votes[code] = votes[code] === choice ? null : choice;
      return delay(pollOf(code), 40);
    },
    toggleLike: (id) => {
      const p = posts.find((x) => x.id === id);
      if (!p) return Promise.reject(new Error(`unknown post ${id}`));
      p.liked = !p.liked;
      p.like += p.liked ? 1 : -1;
      return delay({ ...p }, 40);
    },
  },
};
