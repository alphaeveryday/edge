import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { NotiKind } from '@/api';
import { usePages } from '@/lib/usePages';

export const useNotifications = (kind: NotiKind | 'all') => usePages(['noti', 'list', kind], (c) => api.notification.list(kind, c));
export const useUnreadCount = () => useQuery({ queryKey: ['noti', 'unread'], queryFn: () => api.notification.unread() });

export const useReadNoti = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => api.notification.read(id), onSuccess: () => qc.invalidateQueries({ queryKey: ['noti'] }) });
};
export const useReadAll = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => api.notification.readAll(), onSuccess: () => qc.invalidateQueries({ queryKey: ['noti'] }) });
};
