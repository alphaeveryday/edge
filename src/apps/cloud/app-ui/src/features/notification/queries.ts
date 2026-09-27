import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { NotiKind } from '@/api';

export const useNotifications = (kind: NotiKind | 'all') => useQuery({ queryKey: ['noti', 'list', kind], queryFn: () => api.notification.list(kind) });
export const useUnreadCount = () => useQuery({ queryKey: ['noti', 'unread'], queryFn: () => api.notification.unread() });

export const useReadNoti = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => api.notification.read(id), onSuccess: () => qc.invalidateQueries({ queryKey: ['noti'] }) });
};
export const useReadAll = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => api.notification.readAll(), onSuccess: () => qc.invalidateQueries({ queryKey: ['noti'] }) });
};
