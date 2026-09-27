import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Post } from '@/api';

export const useHotPosts = () => useQuery({ queryKey: ['community', 'hot'], queryFn: () => api.community.hot() });

export const useToggleLike = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.community.toggleLike(id),
    onSuccess: (updated) => {
      qc.setQueryData<Post[]>(['community', 'hot'], (old) => old?.map((p) => (p.id === updated.id ? updated : p)));
    },
  });
};
