/* scope 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Market } from '../explanations/types';
import { scopeRepository } from './index';

const MARKETS_KEY = ['scope', 'markets'];
const STOCKS_KEY = ['scope', 'stocks'];

export function useMarketScopes() {
  return useQuery({ queryKey: MARKETS_KEY, queryFn: () => scopeRepository.listMarkets() });
}

export function useStockScopes() {
  return useQuery({ queryKey: STOCKS_KEY, queryFn: () => scopeRepository.listStocks() });
}

export function useScopeActions() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['scope'] });

  const toggleMarket = useMutation({
    mutationFn: (market: Market) => scopeRepository.toggleMarket(market),
    onSuccess: invalidate,
  });
  const toggleStock = useMutation({
    mutationFn: (code: string) => scopeRepository.toggleStock(code),
    onSuccess: invalidate,
  });

  return { toggleMarket, toggleStock };
}
