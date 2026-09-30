import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useRank = () => useQuery({ queryKey: ['explore', 'rank'], queryFn: () => api.explore.rank() });
