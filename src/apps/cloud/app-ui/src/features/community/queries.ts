import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Me, Poll, PollChoice, Post } from '@/api';
import { useRequireLogin, useSession } from '@/store/session';

export const useMyPosts = () => useQuery({ queryKey: ['community', 'mine'], queryFn: () => api.community.mine() });
export const useUpdateMe = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (patch: Partial<Me>) => api.user.update(patch), onSuccess: (me) => qc.setQueryData(['user', 'me'], me) });
};
export const useMe = () => {
  const loggedIn = useSession((s) => s.loggedIn);
  return useQuery({ queryKey: ['user', 'me'], queryFn: () => api.user.me(), staleTime: Infinity, enabled: loggedIn });
};
export const useHotPosts = () => useQuery({ queryKey: ['community', 'hot'], queryFn: () => api.community.hot() });
export const useEtfPosts = (code: string) => useQuery({ queryKey: ['community', 'posts', code], queryFn: () => api.community.posts(code) });
export const useFeed = (scope: 'all' | 'mine') => useQuery({ queryKey: ['community', 'feed', scope], queryFn: () => api.community.feed(scope) });
export const usePost = (id: string) => useQuery({ queryKey: ['community', 'post', id], queryFn: () => api.community.get(id) });
export const useReplies = (id: string) => useQuery({ queryKey: ['community', 'replies', id], queryFn: () => api.community.replies(id) });
export const usePoll = (code: string, enabled = true) => useQuery({ queryKey: ['community', 'poll', code], queryFn: () => api.community.poll(code), enabled });

const invalidateLists = (qc: ReturnType<typeof useQueryClient>) => {
  qc.invalidateQueries({ queryKey: ['community', 'feed'] });
  qc.invalidateQueries({ queryKey: ['community', 'posts'] });
  qc.invalidateQueries({ queryKey: ['community', 'hot'] });
};

// 좋아요는 회원만. 비로그인이면 유도 시트
export const useToggleLike = () => {
  const qc = useQueryClient();
  const requireLogin = useRequireLogin();
  const m = useMutation({
    mutationFn: (id: string) => api.community.toggleLike(id),
    onSuccess: (updated) => {
      qc.setQueriesData<Post[]>({ queryKey: ['community'], predicate: (q) => q.queryKey[1] !== 'replies' }, (old) => (Array.isArray(old) ? old.map((p) => (p.id === updated.id ? updated : p)) : old));
      qc.setQueryData<Post>(['community', 'post', updated.id], (old) => (old ? { ...old, like: updated.like, liked: updated.liked } : old));
    },
  });
  return { ...m, mutate: (id: string) => requireLogin('좋아요', () => m.mutate(id)) };
};

export const useVote = (code: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (choice: PollChoice) => api.community.vote(code, choice),
    onSuccess: (poll) => qc.setQueryData<Poll>(['community', 'poll', code], poll),
  });
};

export const useReply = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: string) => api.community.reply(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['community', 'replies', id] });
      qc.invalidateQueries({ queryKey: ['community', 'post', id] });
      invalidateLists(qc);
    },
  });
};

export const useCreatePost = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { body: string; tags: string[] }) => api.community.create(v), onSuccess: () => invalidateLists(qc) });
};

export const useDeletePost = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => api.community.remove(id), onSuccess: () => invalidateLists(qc) });
};
