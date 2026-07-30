/* scope 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { Market } from '../explanations/types';
import type { MarketScope, StockScope } from './types';

export interface ScopeRepository {
  listMarkets(): Promise<MarketScope[]>;
  toggleMarket(market: Market): Promise<void>;
  listStocks(): Promise<StockScope[]>;
  toggleStock(code: string): Promise<void>;
}
