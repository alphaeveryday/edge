import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Axis } from '@/api';

export const useDaily = (code: string, pick: { date?: string; week?: string } = {}, enabled = true) =>
  useQuery({
    queryKey: ['analysis', 'daily', code, pick.date ?? (pick.week ? `week:${pick.week}` : 'latest')],
    queryFn: () => api.analysis.daily(code, pick.date, pick.week),
    enabled,
    // 같은 ETF 안의 주 이동만 이전 화면 유지
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[2] === code ? prev : undefined),
  });
export const useFactor = (code: string, axis: Axis) =>
  useQuery({ queryKey: ['analysis', 'factor', code, axis], queryFn: () => api.analysis.factor(code, axis) });
export const useMetric = (code: string, axis: Axis) =>
  useQuery({ queryKey: ['analysis', 'metric', code, axis], queryFn: () => api.analysis.metric(code, axis) });
export const useHint = (key: string | null) =>
  useQuery({ queryKey: ['analysis', 'hint', key], queryFn: () => api.analysis.hint(key!), enabled: !!key });
