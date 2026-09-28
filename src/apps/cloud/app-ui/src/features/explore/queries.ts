import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useRank = () => useQuery({ queryKey: ['explore', 'rank'], queryFn: () => api.explore.rank() });
export const useThemeFeed = (sort: string) => useQuery({ queryKey: ['theme', 'feed', sort], queryFn: () => api.theme.feed(sort) });
export const useThemeDetail = (key: string) => useQuery({ queryKey: ['theme', 'detail', key], queryFn: () => api.theme.detail(key) });
