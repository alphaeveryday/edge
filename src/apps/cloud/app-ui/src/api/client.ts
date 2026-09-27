import type { EtfCode, EtfSummary, HomeBrief, Post, Theme } from './types';

export interface EtfApi {
  get(code: EtfCode): Promise<EtfSummary>;
  list(): Promise<EtfSummary[]>;
}

export interface ThemeApi {
  list(): Promise<Theme[]>;
}

export interface OnboardingApi {
  complete(input: { themes: string[]; etfs: EtfCode[] }): Promise<void>;
}

export interface HomeApi {
  brief(group?: string): Promise<HomeBrief>;
}

export interface CommunityApi {
  hot(): Promise<Post[]>;
  toggleLike(id: string): Promise<Post>;
}

export interface ApiClient {
  etf: EtfApi;
  theme: ThemeApi;
  onboarding: OnboardingApi;
  home: HomeApi;
  community: CommunityApi;
}
