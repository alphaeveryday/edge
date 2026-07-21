/* scope 도메인 — mock 구현. 시안(v0.2)의 종목 유니버스 이식. */
import type { Market } from '../explanations/types';
import type { ScopeRepository } from './repository';
import type { MarketScope, StockScope } from './types';

const markets: Record<Market, boolean> = { KRX: true, NASDAQ: true };

const stocks: StockScope[] = [
  { code: '005930', name: '삼성전자', market: 'KRX', enabled: true },
  { code: '247540', name: '에코프로비엠', market: 'KRX', enabled: true },
  { code: '000660', name: 'SK하이닉스', market: 'KRX', enabled: true },
  { code: 'NVDA', name: 'NVIDIA', market: 'NASDAQ', enabled: true },
  { code: '035720', name: '카카오', market: 'KRX', enabled: true },
  { code: 'TSLA', name: 'Tesla', market: 'NASDAQ', enabled: true },
  { code: '068270', name: '셀트리온', market: 'KRX', enabled: true },
  { code: 'AAPL', name: 'Apple', market: 'NASDAQ', enabled: true },
  { code: '003670', name: '포스코퓨처엠', market: 'KRX', enabled: true },
  { code: '034020', name: '두산에너빌리티', market: 'KRX', enabled: true },
  { code: 'AMD', name: 'AMD', market: 'NASDAQ', enabled: true },
  { code: 'RIVN', name: 'Rivian', market: 'NASDAQ', enabled: true },
  { code: '373220', name: 'LG에너지솔루션', market: 'KRX', enabled: true },
];

export const mockScopeRepository: ScopeRepository = {
  async listMarkets(): Promise<MarketScope[]> {
    return (['KRX', 'NASDAQ'] as const).map((market) => ({
      market,
      enabled: markets[market],
      stockCount: stocks.filter((s) => s.market === market).length,
    }));
  },
  async toggleMarket(market) {
    markets[market] = !markets[market];
  },
  async listStocks(): Promise<StockScope[]> {
    return stocks.map((s) => ({ ...s }));
  },
  async toggleStock(code) {
    const s = stocks.find((x) => x.code === code);
    if (!s) throw new Error('종목을 찾을 수 없습니다.');
    s.enabled = !s.enabled;
  },
};
