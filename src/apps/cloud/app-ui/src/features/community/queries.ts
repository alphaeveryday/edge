import { useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import { api } from '@/api';
import type { Me, Page, VoteStat, VoteChoice, Post, ReportReason } from '@/api';
import { usePages } from '@/lib/usePages';
import { useRequireLogin, useSession } from '@/store/session';

export const useMyPosts = () => usePages(['community', 'mine'], (c) => api.community.mine(c));
export const useUpdateMe = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (patch: Partial<Me>) => api.member.update(patch), onSuccess: (me) => qc.setQueryData(['member', 'me'], me) });
};
export const useMe = () => {
  const loggedIn = useSession((s) => s.loggedIn);
  return useQuery({ queryKey: ['member', 'me'], queryFn: () => api.member.me(), staleTime: Infinity, enabled: loggedIn });
};
export const useHotPosts = () => useQuery({ queryKey: ['community', 'hot'], queryFn: () => api.community.hot() });
export const useEtfPosts = (code: string) => usePages(['community', 'posts', code], (c) => api.community.posts(code, c));
export const useFeed = (scope: 'all' | 'mine') => usePages(['community', 'feed', scope], (c) => api.community.feed(scope, c));
export const usePost = (id: string) => useQuery({ queryKey: ['community', 'post', id], queryFn: () => api.community.get(id) });
export const useReplies = (id: string) => usePages(['community', 'replies', id], (c) => api.community.replies(id, c));
export const useVoteStat = (code: string, enabled = true) => useQuery({ queryKey: ['community', 'voteStat', code], queryFn: () => api.community.voteStat(code), enabled });

const invalidateLists = (qc: ReturnType<typeof useQueryClient>) => {
  qc.invalidateQueries({ queryKey: ['community', 'feed'] });
  qc.invalidateQueries({ queryKey: ['community', 'posts'] });
  qc.invalidateQueries({ queryKey: ['community', 'hot'] });
};

// 비로그인 시 유도 시트로 가는 회원 전용 좋아요
export const useToggleLike = () => {
  const qc = useQueryClient();
  const requireLogin = useRequireLogin();
  const m = useMutation({
    mutationFn: (id: string) => api.community.toggleLike(id),
    onSuccess: (updated) => {
      const swap = (p: Post) => (p.id === updated.id ? updated : p);
      // 인기글 배열과 이어 받는 목록 모두 교체
      qc.setQueriesData<Post[] | InfiniteData<Page<Post>>>({ queryKey: ['community'], predicate: (q) => q.queryKey[1] !== 'replies' }, (old) => {
        if (Array.isArray(old)) return old.map(swap);
        if (old && 'pages' in old) return { ...old, pages: old.pages.map((pg) => ({ ...pg, items: pg.items.map(swap) })) };
        return old;
      });
      qc.setQueryData<Post>(['community', 'post', updated.id], (old) => (old ? { ...old, like: updated.like, liked: updated.liked } : old));
    },
  });
  return { ...m, mutate: (id: string) => requireLogin('좋아요', () => m.mutate(id)) };
};

export const useVote = (code: string) => {
  const qc = useQueryClient();
  return useMutation({
    // null 선택의 철회 요청
    mutationFn: (choice: VoteChoice | null) => (choice ? api.community.vote(code, choice) : api.community.unvote(code)),
    onSuccess: (stat) => qc.setQueryData<VoteStat>(['community', 'voteStat', code], stat),
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

export const useReport = () => useMutation({
  mutationFn: (v: { target: { type: 'post' | 'reply'; id: string }; reason: ReportReason }) => api.community.report(v.target, v.reason),
});

// 차단한 작성자의 글·답글이 목록에서 빠지도록 커뮤니티 조회 전부 갱신
export const useBlock = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (handle: string) => api.community.block(handle), onSuccess: () => qc.invalidateQueries({ queryKey: ['community'] }) });
};
