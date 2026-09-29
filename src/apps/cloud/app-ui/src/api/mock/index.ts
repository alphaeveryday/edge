import type { ApiClient } from '../client';
import { ApiError } from '../error';
import type { Me, VoteStat, VoteChoice, Post, Reply, WatchGroup } from '../types';
import type { Signal } from '@/theme/tokens';
import { SIGNAL_ORDER } from '@/theme/tokens';
import { dailyOf, FACTORS, HINTS, METRICS } from './analysis';
import { chartOf, detailOf, ETF_POSTS, moveOf } from './detail';
import { RANK_META, THEME_DETAILS, THEME_FEED } from './explore';
import { genericIssue, ISSUE_DETAILS, ISSUE_ROWS } from './issues';
import { NOTIFICATIONS } from './notifications';
import { ETFS, GROUP_MEMBERS, GROUPS, POSTS, THEMES } from './data';

const delay = <T,>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

// 앱 재시작 시 초기화되는 인메모리 쓰기 상태
const posts = [...POSTS, ...ETF_POSTS].map((p) => ({ ...p }));
const votes: Record<string, VoteChoice | null> = {};
const ME: Me = { nick: '지수', handle: '@me', avatarBg: '#3D34E0', email: 'jisoo.kim@gmail.com' };
const ACCOUNTS: Record<string, string> = { 'jisoo.kim@gmail.com': 'password' };
const notis = NOTIFICATIONS.map((n) => ({ ...n }));
const replies: Record<string, Reply[]> = {
  p1: [
    { id: 'r1', author: { name: '분할매수중', avatarBg: '#0E8A6C' }, time: '32분', body: '반도체TOP10 저도 같은 생각이에요. 다만 강력 상승 판단이 유지되는지 한 주 더 보려고요.' },
    { id: 'r2', author: { name: '장투합니다', avatarBg: '#8B34E0' }, time: '1시간', body: '저는 숫자가 나온 다음에 들어가요. 지금은 기대만 앞서 있어요.' },
    { id: 'r3', author: { name: '초보투자자', avatarBg: '#E8A13D' }, time: '2시간', body: '이거 초보가 봐도 되는 건가요? 설명 감사합니다.' },
  ],
};
let seq = 100;
const groups = GROUPS.map((g) => ({ ...g }));
const members: Record<string, string[]> = Object.fromEntries(Object.entries(GROUP_MEMBERS).map(([k, v]) => [k, [...v]]));
let recent = ['AXAI', 'DEFN', 'GRID'];

const etfOf = (code: string) => ETFS.find((e) => e.code === code)!;
const groupList = (): WatchGroup[] => groups.map((g) => ({ ...g, count: (members[g.key] ?? []).length }));

const hash = (str: string) => { let h = 2166136261; for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = (h * 16777619) >>> 0; } return h; };
// 종목 코드 시드 분포에 내 표를 더한 투표 현황
const voteStatOf = (code: string): VoteStat => {
  const h = hash(code);
  const buy = 30 + (h % 23), sell = 18 + ((h >> 5) % 17);
  const base = [buy, 100 - buy - sell, sell].map((n) => Math.max(6, n));
  const n0 = 180 + (h % 900);
  const mine = votes[code] ?? null;
  const keys: VoteChoice[] = ['buy', 'wait', 'sell'];
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
      if (!e) return Promise.reject(new ApiError('NOT_FOUND', `unknown etf ${code}`));
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
      if (key === 'base') return Promise.reject(new ApiError('INVALID', '기본 관심은 지울 수 없어요'));
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
    feed: () => delay(THEME_FEED.map((t) => ({ ...t, label: t.key }))),
    detail: (key) => {
      const d = THEME_DETAILS[key];
      return d ? delay({ ...d, label: d.key }) : Promise.reject(new ApiError('NOT_READY', `no theme detail ${key}`));
    },
  },
  explore: {
    rank: () =>
      delay(
        [...ETFS]
          .sort((a, b) => SIGNAL_ORDER.indexOf(b.signal) - SIGNAL_ORDER.indexOf(a.signal) || b.changePct - a.changePct)
          .map((e, i) => ({ etf: e, rank: i + 1, ...(RANK_META[e.code] ?? { title: e.name, chips: [], ready: false }) })),
      ),
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
      return f ? delay(f) : Promise.reject(new ApiError('NOT_READY', `no factor page ${code} ${axis}`));
    },
    metric: (code, axis) => {
      const m = METRICS[code]?.[axis];
      return m ? delay(m) : Promise.reject(new ApiError('NOT_READY', `no metric page ${code} ${axis}`));
    },
    hint: (key) => {
      const h = HINTS[key];
      return h ? delay(h, 40) : Promise.reject(new ApiError('NOT_FOUND', `no hint ${key}`));
    },
  },
  issue: {
    list: (tab) => {
      const mine = members.base ?? [];
      const rows = tab === 'mine' ? ISSUE_ROWS.filter((r) => !r.etf || mine.includes(r.etf.code)) : ISSUE_ROWS;
      return delay(rows);
    },
    get: (id) => {
      const row = ISSUE_ROWS.find((r) => r.id === id);
      if (!row) return Promise.reject(new ApiError('NOT_FOUND', `unknown issue ${id}`));
      const d = ISSUE_DETAILS[id] ?? genericIssue(row);
      const mine = members.base ?? [];
      const affected = d.affectedCodes.map(etfOf).sort((a, b) => Number(mine.includes(b.code)) - Number(mine.includes(a.code)));
      return delay({ ...d, affected });
    },
  },
  member: {
    me: () => delay({ ...ME }, 20),
    update: (patch) => {
      Object.assign(ME, patch);
      return delay({ ...ME }, 40);
    },
    acceptDisclaimer: () => {
      ME.disclaimerAcceptedAt = new Date().toISOString();
      return delay({ ...ME }, 20);
    },
    deleteAccount: () => {
      members.base = [];
      ME.disclaimerAcceptedAt = undefined;
      return delay(undefined, 60);
    },
  },
  auth: {
    login: (email, password) => {
      if (!ACCOUNTS[email] || ACCOUNTS[email] !== password) return Promise.reject(new ApiError('UNAUTHORIZED', '이메일 또는 비밀번호가 맞지 않아요'));
      ME.email = email;
      return delay({ ...ME });
    },
    social: () => delay({ ...ME }),
    signup: ({ email, password, nick }) => {
      if (ACCOUNTS[email]) return Promise.reject(new ApiError('INVALID', '이미 가입된 이메일이에요'));
      ACCOUNTS[email] = password;
      Object.assign(ME, { email, nick, handle: '@' + email.split('@')[0] });
      return delay({ ...ME });
    },
    requestPasswordReset: (email) => (ACCOUNTS[email] ? delay(undefined) : Promise.reject(new ApiError('NOT_FOUND', '가입되지 않은 이메일이에요'))),
    logout: () => delay(undefined, 20),
  },
  notification: {
    list: (kind) => delay(notis.filter((n) => kind === 'all' || n.kind === kind).map((n) => ({ ...n }))),
    unread: () => delay(notis.filter((n) => !n.read).length, 20),
    read: (id) => {
      const n = notis.find((x) => x.id === id);
      if (n) n.read = true;
      return delay(undefined, 20);
    },
    readAll: () => {
      notis.forEach((n) => (n.read = true));
      return delay(undefined, 20);
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
    feed: (scope) => {
      const mine = members.base ?? [];
      const list = posts.filter((p) => scope === 'all' || mine.includes(p.etf.code));
      return delay(list.map((p) => ({ ...p })));
    },
    get: (id) => {
      const p = posts.find((x) => x.id === id);
      return p ? delay({ ...p, views: p.views ?? 7300 }) : Promise.reject(new ApiError('NOT_FOUND', `unknown post ${id}`));
    },
    replies: (id) => delay((replies[id] ?? []).map((r) => ({ ...r }))),
    reply: (id, body) => {
      const r: Reply = { id: 'r' + ++seq, author: { name: ME.nick, avatarBg: ME.avatarBg }, time: '방금', body };
      replies[id] = [...(replies[id] ?? []), r];
      const p = posts.find((x) => x.id === id);
      if (p) p.reply += 1;
      return delay(r, 40);
    },
    create: ({ body, tags }) => {
      const e = tags[0] ? etfOf(tags[0]) : undefined;
      const post: Post = {
        id: 'u' + ++seq,
        etf: e ? { code: e.code, theme: e.theme, logoBg: e.logoBg, short: e.name.replace(/^(TIGER|KODEX|PLUS|HANARO|SOL)\s*/, '') } : { code: '', theme: '', logoBg: '#8E8E93', short: '' },
        author: { name: ME.nick, handle: ME.handle, avatarBg: ME.avatarBg },
        time: '방금', body, like: 0, reply: 0, repost: 0, liked: false, views: 0, mine: true,
      };
      posts.unshift(post);
      return delay({ ...post }, 60);
    },
    mine: () => delay(posts.filter((p) => p.mine).map((p) => ({ ...p }))),
    remove: (id) => {
      const i = posts.findIndex((x) => x.id === id);
      if (i >= 0) posts.splice(i, 1);
      return delay(undefined, 40);
    },
    posts: (code) => delay(posts.filter((p) => p.etf.code === code && !p.id.startsWith('p')).map((p) => ({ ...p }))),
    voteStat: (code) => delay(voteStatOf(code)),
    vote: (code, choice) => {
      votes[code] = votes[code] === choice ? null : choice;
      return delay(voteStatOf(code), 40);
    },
    toggleLike: (id) => {
      const p = posts.find((x) => x.id === id);
      if (!p) return Promise.reject(new ApiError('NOT_FOUND', `unknown post ${id}`));
      p.liked = !p.liked;
      p.like += p.liked ? 1 : -1;
      return delay({ ...p }, 40);
    },
  },
};
