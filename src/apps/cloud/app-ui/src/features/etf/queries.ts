import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useWatchEtfs = () => useQuery({ queryKey: ['etf', 'watch'], queryFn: () => api.etf.listWatch() });
export const useEtf = (code: string) => useQuery({ queryKey: ['etf', code], queryFn: () => api.etf.get(code) });
