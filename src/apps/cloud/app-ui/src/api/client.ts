import type { Axis, ChartData, Notification, NotiKind, Me, Reply, RankRow, DailyAnalysis, EtfCode, EtfDetailData, EtfSummary, FactorPage, Hint, HomeBrief, MetricPage, MoveInfo, VoteStat, VoteChoice, Post, Theme, WatchGroup } from './types';

export interface EtfApi {
  get(code: EtfCode): Promise<EtfSummary>;
  list(): Promise<EtfSummary[]>;
  search(q: string): Promise<EtfSummary[]>;
  recent(): Promise<EtfSummary[]>;
  chart(code: EtfCode, range: string): Promise<ChartData>;
  move(code: EtfCode): Promise<MoveInfo>;
  detail(code: EtfCode): Promise<EtfDetailData>;
}

export interface WatchApi {
  groups(): Promise<WatchGroup[]>;
  list(group: string): Promise<EtfSummary[]>;
  createGroup(label: string): Promise<WatchGroup>;
  deleteGroup(key: string): Promise<void>;
  setMembers(group: string, codes: EtfCode[]): Promise<void>;
  membership(code: EtfCode): Promise<string[]>;
  setMembership(code: EtfCode, groups: string[]): Promise<void>;
}

export interface ThemeApi {
  list(): Promise<Theme[]>;
}

export interface ExploreApi {
  rank(): Promise<RankRow[]>;
}

export interface OnboardingApi {
  complete(input: { themes: string[]; etfs: EtfCode[] }): Promise<void>;
}

export interface AnalysisApi {
  daily(code: EtfCode, date?: string): Promise<DailyAnalysis>;
  factor(code: EtfCode, axis: Axis): Promise<FactorPage>;
  metric(code: EtfCode, axis: Axis): Promise<MetricPage>;
  hint(key: string): Promise<Hint>;
}

export interface HomeApi {
  brief(group?: string): Promise<HomeBrief>;
}

export interface CommunityApi {
  hot(): Promise<Post[]>;
  posts(code: EtfCode): Promise<Post[]>;
  feed(scope: 'all' | 'mine'): Promise<Post[]>;
  get(id: string): Promise<Post>;
  replies(id: string): Promise<Reply[]>;
  reply(id: string, body: string): Promise<Reply>;
  create(input: { body: string; tags: EtfCode[] }): Promise<Post>;
  mine(): Promise<Post[]>;
  remove(id: string): Promise<void>;
  toggleLike(id: string): Promise<Post>;
  voteStat(code: EtfCode): Promise<VoteStat>;
  vote(code: EtfCode, choice: VoteChoice): Promise<VoteStat>;
}

export interface MemberApi {
  me(): Promise<Me>;
  update(patch: Partial<Me>): Promise<Me>;
  acceptDisclaimer(): Promise<Me>;
  deleteAccount(): Promise<void>;
}

export interface AuthApi {
  login(email: string, password: string): Promise<Me>;
  signup(input: { email: string; password: string; nick: string }): Promise<Me>;
  requestPasswordReset(email: string): Promise<void>;
  confirmPasswordReset(email: string, code: string, newPassword: string): Promise<void>;
  logout(): Promise<void>;
}

export interface NotificationApi {
  list(kind: NotiKind | 'all'): Promise<Notification[]>;
  unread(): Promise<number>;
  read(id: string): Promise<void>;
  readAll(): Promise<void>;
}

export interface ApiClient {
  etf: EtfApi;
  watch: WatchApi;
  theme: ThemeApi;
  explore: ExploreApi;
  onboarding: OnboardingApi;
  analysis: AnalysisApi;
  home: HomeApi;
  community: CommunityApi;
  member: MemberApi;
  auth: AuthApi;
  notification: NotificationApi;
}
