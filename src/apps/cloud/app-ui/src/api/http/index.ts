import type { ApiClient } from '../client';
import { HINTS } from '../mock/analysis';
import { ApiError } from '../error';
import type { ChartData, DailyAnalysis, EtfDetailData, EtfSummary, FactorPage, HomeBrief, MetricPage, MoveInfo, Notification, VoteChoice, RankRow, WatchGroup } from '../types';
import { request } from './fetch';
import * as m from './map';
import { tokens } from './storage';

interface WireAuth { accessToken: string; refreshToken: string; me: m.WireMe; guestMapped?: boolean }
const signIn = async (r: WireAuth) => { await tokens.save(r.accessToken, r.refreshToken); return m.me(r.me); };

// 서버에 없는 최근 본 ETF의 메모리 보관
let recent: EtfSummary[] = [];

// 게스트의 내 투표 조회 생략
const myVote = async (code: string) =>
  (await tokens.access()) ? (await request<{ choice: VoteChoice | null }>('GET', `/etfs/${code}/vote`)).choice : null;

// 와이어 테마 key 의 화면용 라벨 변환
let themeLabels: Promise<Record<string, string>> | null = null;
const labels = () => (themeLabels ??= request<m.WireTheme[]>('GET', '/themes', { auth: false }).then((l) => Object.fromEntries(l.map((t) => [t.key, t.label]))));
const labelOf = async (key: string) => (await labels())[key] ?? key;
const relabel = async <T extends { theme: string }>(x: T): Promise<T> => ({ ...x, theme: await labelOf(x.theme) });
const summary = async (e: m.WireEtfSummary) => relabel(m.etf(e));
const summaries = (list: m.WireEtfSummary[]) => Promise.all(list.map(summary));
const post = async (p: m.WirePost) => { const x = m.post(p); return { ...x, etf: await relabel(x.etf) }; };
const posts = (list: m.WirePost[]) => Promise.all(list.map(post));
const postPage = async (p: m.WirePage<m.WirePost>) => ({ items: await posts(p.items), next: p.nextCursor });

export const httpClient: ApiClient = {
  etf: {
    get: async (code) => {
      const e = await summary(await request<m.WireEtfSummary>('GET', `/etfs/${code}`));
      recent = [e, ...recent.filter((r) => r.code !== code)].slice(0, 5);
      return e;
    },
    list: async () => summaries(await request('GET', '/etfs', { auth: false })),
    search: async (q) => (q.trim() ? summaries(await request('GET', '/etfs', { query: { q: q.trim() }, auth: false })) : []),
    recent: async () => recent,
    chart: (code, range) => request<ChartData>('GET', `/etfs/${code}/chart`, { query: { range } }),
    move: async (code) => {
      const { at, ...rest } = await request<Omit<MoveInfo, 'ago'> & { at: string }>('GET', `/etfs/${code}/move`);
      return { ...rest, ago: m.ago(at) };
    },
    detail: (code) => request<EtfDetailData>('GET', `/etfs/${code}/detail`),
  },
  watch: {
    groups: () => request<WatchGroup[]>('GET', '/watch-groups'),
    list: async (group) => summaries(await request('GET', `/watch-groups/${group}/etfs`)),
    createGroup: (label) => request<WatchGroup>('POST', '/watch-groups', { body: { label } }),
    deleteGroup: (key) => request<void>('DELETE', `/watch-groups/${key}`),
    setMembers: (group, codes) => request<void>('PUT', `/watch-groups/${group}/etfs`, { body: { codes } }),
    membership: (code) => request<string[]>('GET', `/etfs/${code}/watch-groups`),
    setMembership: (code, groups) => request<void>('PUT', `/etfs/${code}/watch-groups`, { body: { groups } }),
  },
  theme: {
    list: async () => (await request<m.WireTheme[]>('GET', '/themes', { auth: false })).map(m.theme),
  },
  explore: {
    rank: async () => Promise.all((await request<(Omit<RankRow, 'etf'> & { etf: m.WireEtfSummary })[]>('GET', '/explore/rank', { auth: false })).map(async (r) => ({ ...r, etf: await summary(r.etf) }))),
  },
  onboarding: {
    complete: (input) => request<void>('POST', '/onboarding/complete', { body: input }),
  },
  analysis: {
    daily: async (code, date) => {
      const d = await request<Omit<DailyAnalysis, 'axes'> & { axes: { axis: string; dir: DailyAnalysis['axes'][number]['dir']; summary: string; hasPage: boolean }[] }>('GET', `/etfs/${code}/analysis`, { query: { date }, auth: false });
      return { ...d, axes: d.axes.map((a) => ({ ...a, axis: m.axisLabel(a.axis) })) };
    },
    factor: async (code, axis) => {
      const f = await request<Omit<FactorPage, 'axis'> & { axis: string }>('GET', `/etfs/${code}/analysis/factors/${m.axisCode(axis)}`, { auth: false });
      return { ...f, axis: m.axisLabel(f.axis) };
    },
    metric: async (code, axis) => {
      const p = await request<Omit<MetricPage, 'axis'> & { axis: string }>('GET', `/etfs/${code}/analysis/metrics/${m.axisCode(axis)}`, { auth: false });
      return { ...p, axis: m.axisLabel(p.axis) };
    },
    hint: async (key) => {
      const h = HINTS[key];
      if (!h) throw new ApiError('NOT_FOUND', `no hint ${key}`);
      return h;
    },
  },
  home: {
    brief: async (group) => {
      const b = await request<Omit<HomeBrief, 'etfs'> & { etfs: m.WireEtfSummary[] }>('GET', '/home/brief', { query: { group } });
      return { ...b, etfs: await summaries(b.etfs) };
    },
  },
  community: {
    hot: async () => posts((await request<m.WirePage<m.WirePost>>('GET', '/posts', { query: { scope: 'hot' } })).items),
    posts: async (code, cursor) => postPage(await request<m.WirePage<m.WirePost>>('GET', '/posts', { query: { code, cursor } })),
    feed: async (scope, cursor) => postPage(await request<m.WirePage<m.WirePost>>('GET', '/posts', { query: { scope, cursor } })),
    mine: async (cursor) => postPage(await request<m.WirePage<m.WirePost>>('GET', '/posts', { query: { scope: 'mine', cursor } })),
    // 내 좋아요와 내 글 판정용 요청자 동봉
    get: async (id) => post(await request<m.WirePost>('GET', `/posts/${id}`)),
    // 차단한 작성자 제외용 요청자 동봉
    replies: async (id, cursor) => {
      const p = await request<m.WirePage<m.WireReply>>('GET', `/posts/${id}/replies`, { query: { cursor } });
      return { items: p.items.map(m.reply), next: p.nextCursor };
    },
    reply: async (id, body) => m.reply(await request<m.WireReply>('POST', `/posts/${id}/replies`, { body: { body } })),
    create: async (input) => post(await request<m.WirePost>('POST', '/posts', { body: input })),
    remove: (id) => request<void>('DELETE', `/posts/${id}`),
    report: (target, reason) => request<void>('POST', '/reports', { body: { targetType: target.type, targetId: target.id, reason } }),
    block: (handle) => request<void>('PUT', `/blocks/${encodeURIComponent(handle)}`),
    // 현재 좋아요 여부 조회 후 추가와 취소 분기
    toggleLike: async (id) => {
      const cur = await request<m.WirePost>('GET', `/posts/${id}`);
      return post(await request<m.WirePost>(cur.liked ? 'DELETE' : 'PUT', `/posts/${id}/like`));
    },
    voteStat: async (code) => {
      const [count, mine] = await Promise.all([request<m.WireVoteCount>('GET', `/etfs/${code}/vote/count`, { auth: false }), myVote(code)]);
      return m.voteStat(code, count, mine);
    },
    // 현황 없는 응답이라 성공 후 집계 재조회
    vote: async (code, choice) => {
      await request<void>('PUT', `/etfs/${code}/vote`, { body: { choice } });
      return m.voteStat(code, await request<m.WireVoteCount>('GET', `/etfs/${code}/vote/count`, { auth: false }), choice);
    },
    unvote: async (code) => {
      await request<void>('DELETE', `/etfs/${code}/vote`);
      return m.voteStat(code, await request<m.WireVoteCount>('GET', `/etfs/${code}/vote/count`, { auth: false }), null);
    },
  },
  member: {
    me: async () => m.me(await request<m.WireMe>('GET', '/me')),
    update: async (patch) => m.me(await request<m.WireMe>('PATCH', '/me', { body: { nick: patch.nick } })),
    acceptDisclaimer: async () => m.me(await request<m.WireMe>('POST', '/me/disclaimer')),
    deleteAccount: async () => { await request<void>('DELETE', '/me'); await tokens.clear(); },
  },
  auth: {
    login: async (email, password) => signIn(await request<WireAuth>('POST', '/auth/login', { body: { email, password }, auth: 'device' })),
    sendSignupCode: (email) => request<void>('POST', '/auth/signup/code', { body: { email }, auth: false }),
    signup: async (input) => signIn(await request<WireAuth>('POST', '/auth/signup', { body: input, auth: 'device' })),
    requestPasswordReset: (email) => request<void>('POST', '/auth/password-reset', { body: { email }, auth: false }),
    confirmPasswordReset: (email, code, newPassword) => request<void>('POST', '/auth/password-reset/confirm', { body: { email, code, newPassword }, auth: false }),
    logout: async () => {
      try { await request<void>('POST', '/auth/logout'); } finally { await tokens.clear(); }
    },
  },
  notification: {
    list: async (kind, cursor) => {
      const p = await request<m.WirePage<Notification>>('GET', '/notifications', { query: { kind: kind === 'all' ? undefined : kind, cursor } });
      return { items: p.items.map((n) => ({ ...n, time: m.ago(n.time) })), next: p.nextCursor };
    },
    unread: async () => (await request<{ count: number }>('GET', '/notifications/unread-count')).count,
    read: (id) => request<void>('POST', `/notifications/${id}/read`),
    readAll: () => request<void>('POST', '/notifications/read-all'),
  },
};
