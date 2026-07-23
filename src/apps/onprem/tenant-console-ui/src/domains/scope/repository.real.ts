/* scope 도메인 — tenant-console-api 실연동 구현 */
import { apiClient } from '../../api/client';
import type { ScopeRepository } from './repository';
import type { MarketScope, StockScope } from './types';

export const realScopeRepository: ScopeRepository = {
  listMarkets: () => apiClient.get<MarketScope[]>('/scope/markets'),
  toggleMarket: (market) => apiClient.post<void>(`/scope/markets/${market}/toggle`),
  listStocks: () => apiClient.get<StockScope[]>('/scope/stocks'),
  toggleStock: (code) => apiClient.post<void>(`/scope/stocks/${code}/toggle`),
};
