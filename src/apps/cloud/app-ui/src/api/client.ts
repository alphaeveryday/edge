import type { Axis, ChartData, DailyAnalysis, EtfCode, EtfDetailData, EtfSummary, FactorPage, Hint, HomeBrief, MetricPage, MoveInfo, Poll, PollChoice, Post, Theme, WatchGroup } from './types';

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
  toggleLike(id: string): Promise<Post>;
  poll(code: EtfCode): Promise<Poll>;
  vote(code: EtfCode, choice: PollChoice): Promise<Poll>;
}

export interface ApiClient {
  etf: EtfApi;
  watch: WatchApi;
  theme: ThemeApi;
  onboarding: OnboardingApi;
  analysis: AnalysisApi;
  home: HomeApi;
  community: CommunityApi;
}
