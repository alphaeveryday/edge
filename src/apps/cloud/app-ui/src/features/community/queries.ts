import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Poll, PollChoice, Post } from '@/api';

export const useHotPosts = () => useQuery({ queryKey: ['community', 'hot'], queryFn: () => api.community.hot() });
export const useEtfPosts = (code: string) => useQuery({ queryKey: ['community', 'posts', code], queryFn: () => api.community.posts(code) });
export const usePoll = (code: string) => useQuery({ queryKey: ['community', 'poll', code], queryFn: () => api.community.poll(code) });

export const useToggleLike = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.community.toggleLike(id),
    onSuccess: (updated) => {
      qc.setQueriesData<Post[]>({ queryKey: ['community'] }, (old) => (Array.isArray(old) ? old.map((p) => (p.id === updated.id ? updated : p)) : old));
    },
  });
};

export const useVote = (code: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (choice: PollChoice) => api.community.vote(code, choice),
    onSuccess: (poll) => qc.setQueryData<Poll>(['community', 'poll', code], poll),
  });
};
