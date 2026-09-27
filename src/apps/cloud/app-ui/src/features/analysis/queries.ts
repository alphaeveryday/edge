import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Axis } from '@/api';

export const useDaily = (code: string, date?: string) =>
  useQuery({ queryKey: ['analysis', 'daily', code, date ?? 'latest'], queryFn: () => api.analysis.daily(code, date) });
export const useFactor = (code: string, axis: Axis) =>
  useQuery({ queryKey: ['analysis', 'factor', code, axis], queryFn: () => api.analysis.factor(code, axis) });
export const useMetric = (code: string, axis: Axis) =>
  useQuery({ queryKey: ['analysis', 'metric', code, axis], queryFn: () => api.analysis.metric(code, axis) });
export const useHint = (key: string | null) =>
  useQuery({ queryKey: ['analysis', 'hint', key], queryFn: () => api.analysis.hint(key!), enabled: !!key });
