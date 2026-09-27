import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useEtf = (code: string) => useQuery({ queryKey: ['etf', code], queryFn: () => api.etf.get(code) });
