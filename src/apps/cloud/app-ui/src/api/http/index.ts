import type { ApiClient } from '../client';
import { HINTS } from '../mock/analysis';
import { ApiError } from '../error';
import type { ChartData, DailyAnalysis, EtfDetailData, EtfSummary, FactorPage, HomeBrief, IssueDetail, IssueRow, MetricPage, MoveInfo, Notification, PollChoice, RankRow, WatchGroup } from '../types';
import { request } from './fetch';
import * as m from './map';
import { tokens } from './storage';

interface WireAuth { accessToken: string; refreshToken: string; me: m.WireMe; guestMapped?: boolean }
const signIn = async (r: WireAuth) => { await tokens.save(r.accessToken, r.refreshToken); return m.me(r.me); };

// 서버가 없는 것: 최근 본 ETF(기기 보관), 용어 힌트(앱 번들), 내 투표 선택(응답에 없음)
let recent: EtfSummary[] = [];
const myVotes: Record<string, PollChoice | null> = {};

const summaries = (list: m.WireEtfSummary[]) => list.map(m.etf);

export const httpClient: ApiClient = {
  etf: {
    get: async (code) => {
      const e = m.etf(await request<m.WireEtfSummary>('GET', `/etfs/${code}`));
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
    // 앱 파라미터명 sort 는 계약의 방향 필터 dir
    feed: async (sort) => (await request<m.WireThemeFeedItem[]>('GET', '/themes/feed', { query: { dir: sort || 'all' }, auth: false })).map(m.themeFeedItem),
    detail: async (key) => m.themeDetail(await request<m.WireThemeDetail>('GET', `/themes/${encodeURIComponent(key)}`, { auth: false })),
  },
  explore: {
    rank: async () => (await request<(Omit<RankRow, 'etf'> & { etf: m.WireEtfSummary })[]>('GET', '/explore/rank', { auth: false })).map((r) => ({ ...r, etf: m.etf(r.etf) })),
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
      return { ...b, asOf: m.asOfLabel(b.asOf), etfs: summaries(b.etfs) };
    },
  },
  community: {
    hot: async () => (await request<m.WirePage<m.WirePost>>('GET', '/posts', { query: { scope: 'hot' } })).items.map(m.post),
    posts: async (code) => (await request<m.WirePage<m.WirePost>>('GET', '/posts', { query: { code } })).items.map(m.post),
    feed: async (scope) => (await request<m.WirePage<m.WirePost>>('GET', '/posts', { query: { scope } })).items.map(m.post),
    mine: async () => (await request<m.WirePage<m.WirePost>>('GET', '/posts', { query: { scope: 'mine' } })).items.map(m.post),
    // 공개 조회지만 요청자를 보내야 liked·mine 이 채워진다
    get: async (id) => m.post(await request<m.WirePost>('GET', `/posts/${id}`)),
    replies: async (id) => (await request<m.WirePage<m.WireReply>>('GET', `/posts/${id}/replies`, { auth: false })).items.map(m.reply),
    reply: async (id, body) => m.reply(await request<m.WireReply>('POST', `/posts/${id}/replies`, { body: { body } })),
    create: async (input) => m.post(await request<m.WirePost>('POST', '/posts', { body: input })),
    remove: (id) => request<void>('DELETE', `/posts/${id}`),
    // 계약은 PUT/DELETE 두 개. 현재 liked 를 모르는 호출자라 조회 후 분기
    toggleLike: async (id) => {
      const cur = await request<m.WirePost>('GET', `/posts/${id}`);
      return m.post(await request<m.WirePost>(cur.liked ? 'DELETE' : 'PUT', `/posts/${id}/like`));
    },
    poll: async (code) => m.poll(code, await request<m.WireVoteCount>('GET', `/etfs/${code}/vote/count`, { auth: false }), myVotes[code] ?? null),
    // 응답에 현황이 없어 성공 후 count 를 다시 읽는다
    vote: async (code, choice) => {
      await request<void>('PUT', `/etfs/${code}/vote`, { body: { choice } });
      myVotes[code] = choice;
      return m.poll(code, await request<m.WireVoteCount>('GET', `/etfs/${code}/vote/count`, { auth: false }), choice);
    },
  },
  issue: {
    list: async (tab) => (await request<m.WirePage<IssueRow>>('GET', '/issues', { query: { tab } })).items.map((r) => ({ ...r, etf: r.etf ? { ...r.etf, logoBg: m.bgOf(r.etf.theme) } : undefined })),
    get: async (id) => {
      const d = await request<Omit<IssueDetail, 'affected'> & { affected: (m.WireEtfSummary & { prev?: IssueDetail['affected'][number]['prev'] })[] }>('GET', `/issues/${id}`, { auth: false });
      return { ...d, affected: d.affected.map((a) => ({ ...m.etf(a), prev: a.prev })) };
    },
  },
  user: {
    me: async () => m.me(await request<m.WireMe>('GET', '/me')),
    update: async (patch) => m.me(await request<m.WireMe>('PATCH', '/me', { body: { nick: patch.nick, handle: patch.handle } })),
    acceptDisclaimer: async () => m.me(await request<m.WireMe>('POST', '/me/disclaimer')),
    deleteAccount: async () => { await request<void>('DELETE', '/me'); await tokens.clear(); },
  },
  auth: {
    login: async (email, password) => signIn(await request<WireAuth>('POST', '/auth/login', { body: { email, password }, auth: 'device' })),
    // 플랫폼 idToken 발급(expo-apple-authentication 등)은 아직 없다. 스텁 서버는 값을 보지 않는다
    social: async (provider) => signIn(await request<WireAuth>('POST', '/auth/social', { body: { provider, idToken: 'todo' }, auth: 'device' })),
    signup: async (input) => signIn(await request<WireAuth>('POST', '/auth/signup', { body: input, auth: 'device' })),
    requestPasswordReset: (email) => request<void>('POST', '/auth/password-reset', { body: { email }, auth: false }),
    logout: async () => {
      try { await request<void>('POST', '/auth/logout'); } finally { await tokens.clear(); }
    },
  },
  notification: {
    list: async (kind) => (await request<m.WirePage<Notification>>('GET', '/notifications', { query: { kind: kind === 'all' ? undefined : kind } })).items.map((n) => ({ ...n, time: m.ago(n.time) })),
    unread: async () => (await request<{ count: number }>('GET', '/notifications/unread-count')).count,
    read: (id) => request<void>('POST', `/notifications/${id}/read`),
    readAll: () => request<void>('POST', '/notifications/read-all'),
  },
};
