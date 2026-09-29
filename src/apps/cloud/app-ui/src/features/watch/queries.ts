import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';

const invalidate = (qc: ReturnType<typeof useQueryClient>) => {
  qc.invalidateQueries({ queryKey: ['watch'] });
  qc.invalidateQueries({ queryKey: ['home'] });
};

export const useWatchGroups = () => useQuery({ queryKey: ['watch', 'groups'], queryFn: () => api.watch.groups() });
export const useWatchList = (group: string) => useQuery({ queryKey: ['watch', 'list', group], queryFn: () => api.watch.list(group) });
export const useMembership = (code: string) => useQuery({ queryKey: ['watch', 'membership', code], queryFn: () => api.watch.membership(code), enabled: !!code });

export const useCreateGroup = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (label: string) => api.watch.createGroup(label), onSuccess: () => invalidate(qc) });
};
export const useDeleteGroup = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (key: string) => api.watch.deleteGroup(key), onSuccess: () => invalidate(qc) });
};
export const useSetMembers = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { group: string; codes: string[] }) => api.watch.setMembers(v.group, v.codes), onSuccess: () => invalidate(qc) });
};
export const useSetMembership = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { code: string; groups: string[] }) => api.watch.setMembership(v.code, v.groups), onSuccess: () => invalidate(qc) });
};
