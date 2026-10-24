import { useQuery } from '@tanstack/react-query';
import { api, isApiError } from '@/api';

export const useEtf = (code: string) => useQuery({ queryKey: ['etf', code], queryFn: () => api.etf.get(code), enabled: !!code });
export const useEtfList = () => useQuery({ queryKey: ['etf', 'list'], queryFn: () => api.etf.list() });
export const useEtfSearch = (q: string) => useQuery({ queryKey: ['etf', 'search', q], queryFn: () => api.etf.search(q), enabled: q.trim().length > 0 });
export const useRecentEtfs = () => useQuery({ queryKey: ['etf', 'recent'], queryFn: () => api.etf.recent() });
export const useChart = (code: string, range = '1M') => useQuery({ queryKey: ['etf', 'chart', code, range], queryFn: () => api.etf.chart(code, range) });
// 원천 없는 ETF 의 재시도 생략
export const useMove = (code: string) => useQuery({ queryKey: ['etf', 'move', code], queryFn: () => api.etf.move(code),
  retry: (n, e) => !isApiError(e, 'NOT_READY') && n < 3 });
export const useEtfDetail = (code: string) => useQuery({ queryKey: ['etf', 'detail', code], queryFn: () => api.etf.detail(code) });
