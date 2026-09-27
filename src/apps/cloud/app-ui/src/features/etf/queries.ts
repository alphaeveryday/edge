import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useEtf = (code: string) => useQuery({ queryKey: ['etf', code], queryFn: () => api.etf.get(code) });
export const useEtfList = () => useQuery({ queryKey: ['etf', 'list'], queryFn: () => api.etf.list() });
export const useThemes = () => useQuery({ queryKey: ['theme', 'list'], queryFn: () => api.theme.list() });
