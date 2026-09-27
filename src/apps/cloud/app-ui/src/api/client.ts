import type { EtfCode, EtfSummary, HomeBrief, Post } from './types';

export interface EtfApi {
  get(code: EtfCode): Promise<EtfSummary>;
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
  home: HomeApi;
  community: CommunityApi;
}
