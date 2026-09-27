import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useRank = () => useQuery({ queryKey: ['explore', 'rank'], queryFn: () => api.explore.rank() });
export const useThemeSheet = (theme: string | null) =>
  useQuery({ queryKey: ['theme', 'sheet', theme], queryFn: () => api.theme.sheet(theme!), enabled: !!theme });
export const useThemeFeed = (sort: string) => useQuery({ queryKey: ['theme', 'feed', sort], queryFn: () => api.theme.feed(sort) });
export const useThemeDetail = (key: string) => useQuery({ queryKey: ['theme', 'detail', key], queryFn: () => api.theme.detail(key) });
