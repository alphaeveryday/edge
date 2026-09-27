import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useHomeBrief = (group?: string) =>
  useQuery({ queryKey: ['home', 'brief', group ?? 'base'], queryFn: () => api.home.brief(group) });
