import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';

const invalidate = (qc: ReturnType<typeof useQueryClient>) => {
  qc.invalidateQueries({ queryKey: ['watch'] });
  qc.invalidateQueries({ queryKey: ['home'] });
};

export const useWatchGroups = () => useQuery({ queryKey: ['watch', 'groups'], queryFn: () => api.watch.groups() });
export const useWatchList = (group: string) => useQuery({ queryKey: ['watch', 'list', group], queryFn: () => api.watch.list(group) });
// 모든 관심 그룹에 담긴 ETF 코드
export const useWatchedCodes = () => {
  const { data: groups } = useWatchGroups();
  return useQueries({
    queries: (groups ?? []).map((g) => ({ queryKey: ['watch', 'list', g.key], queryFn: () => api.watch.list(g.key) })),
    combine: (rs) => ({ codes: new Set(rs.flatMap((r) => r.data ?? []).map((e) => e.code)), ready: !!groups && rs.every((r) => r.isSuccess) }),
  });
};
// 그룹별 담긴 ETF 코드
export const useGroupMembers = () => {
  const { data: groups } = useWatchGroups();
  return useQueries({
    queries: (groups ?? []).map((g) => ({ queryKey: ['watch', 'list', g.key], queryFn: () => api.watch.list(g.key) })),
    combine: (rs) => Object.fromEntries((groups ?? []).flatMap((g, i) => (rs[i]?.data ? [[g.key, new Set(rs[i].data.map((e) => e.code))]] : []))) as Record<string, Set<string>>,
  });
};
export const useMembership = (code: string) => useQuery({ queryKey: ['watch', 'membership', code], queryFn: () => api.watch.membership(code), enabled: !!code });

export const useCreateGroup = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (label: string) => api.watch.createGroup(label), onSuccess: () => qc.invalidateQueries({ queryKey: ['watch', 'groups'] }) });
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
  return useMutation({
    mutationFn: (items: { code: string; groups: string[] }[]) => Promise.all(items.map((v) => api.watch.setMembership(v.code, v.groups))),
    onSuccess: () => invalidate(qc),
  });
};
