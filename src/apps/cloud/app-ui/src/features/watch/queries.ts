import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type EtfSummary, type WatchGroup } from '@/api';
import { track } from '@/lib/analytics';

const invalidate = (qc: ReturnType<typeof useQueryClient>) => {
  qc.invalidateQueries({ queryKey: ['watch'] });
  qc.invalidateQueries({ queryKey: ['home'] });
};

// 캐시의 그룹별 담긴 코드에 변경을 적용한 담김 변화 기록
// 순서만 바뀐 저장은 제외
const trackWatch = (qc: ReturnType<typeof useQueryClient>, apply: (members: Record<string, Set<string>>) => void) => {
  const groups = qc.getQueryData<WatchGroup[]>(['watch', 'groups']) ?? [];
  const before = Object.fromEntries(groups.map((g) => [g.key, new Set((qc.getQueryData<EtfSummary[]>(['watch', 'list', g.key]) ?? []).map((e) => e.code))]));
  const after = Object.fromEntries(Object.entries(before).map(([k, v]) => [k, new Set(v)]));
  apply(after);
  const same = Object.keys(after).every((k) => before[k]?.size === after[k].size && [...after[k]].every((c) => before[k].has(c)));
  if (same) return;
  track('watchlist_updated', { etf_count: new Set(Object.values(after).flatMap((v) => [...v])).size });
};

export const useWatchGroups = () => useQuery({ queryKey: ['watch', 'groups'], queryFn: () => api.watch.groups() });
export const useWatchList = (group: string, keep = false) =>
  useQuery({ queryKey: ['watch', 'list', group], queryFn: () => api.watch.list(group), placeholderData: keep ? keepPreviousData : undefined });
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
  return useMutation({
    mutationFn: (v: { group: string; codes: string[] }) => api.watch.setMembers(v.group, v.codes),
    onSuccess: (_, v) => {
      trackWatch(qc, (m) => { m[v.group] = new Set(v.codes); });
      invalidate(qc);
    },
  });
};
export const useSetMembership = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: { code: string; groups: string[] }[]) => Promise.all(items.map((v) => api.watch.setMembership(v.code, v.groups))),
    onSuccess: (_, items) => {
      trackWatch(qc, (m) => items.forEach((v) => Object.entries(m).forEach(([k, set]) => (v.groups.includes(k) ? set.add(v.code) : set.delete(v.code)))));
      invalidate(qc);
    },
  });
};
