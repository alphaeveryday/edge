import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useRank = () => useQuery({ queryKey: ['explore', 'rank'], queryFn: () => api.explore.rank() });
export const useThemeFeed = () => useQuery({ queryKey: ['theme', 'feed'], queryFn: () => api.theme.feed() });
export const useThemeDetail = (key: string) => useQuery({ queryKey: ['theme', 'detail', key], queryFn: () => api.theme.detail(key) });
