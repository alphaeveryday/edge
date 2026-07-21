/* scope 도메인 — 제공 범위 (시장·종목 단위 제공 여부). mock·real 공유 타입. */
import type { Market } from '../explanations/types';

export interface MarketScope {
  market: Market;
  enabled: boolean;
  stockCount: number;
}

export interface StockScope {
  code: string;
  name: string;
  market: Market;
  /** 종목 단위 제공 여부 (시장 비활성 시 무시되고 전체 미제공) */
  enabled: boolean;
}
