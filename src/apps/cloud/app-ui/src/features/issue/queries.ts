import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';

export const useIssues = (tab: 'mine' | 'all') => useQuery({ queryKey: ['issue', 'list', tab], queryFn: () => api.issue.list(tab) });
export const useIssue = (id: string) => useQuery({ queryKey: ['issue', id], queryFn: () => api.issue.get(id) });
