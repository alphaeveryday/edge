import type { EtfCode, EtfSummary } from './types';

export interface EtfApi {
  listWatch(): Promise<EtfSummary[]>;
  get(code: EtfCode): Promise<EtfSummary>;
}

export interface ApiClient {
  etf: EtfApi;
}
