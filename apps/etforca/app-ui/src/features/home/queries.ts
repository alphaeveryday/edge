import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { api } from '@/api';
import { useSessionQuery } from '@/lib/useSessionQuery';

// 하루 한 번 바뀌는 지수의 10분 재사용
export const useIndices = () => useQuery({ queryKey: ['home', 'indices'], queryFn: () => api.home.indices(), staleTime: 10 * 60_000 });

const briefKey = (group?: string) => ['home', 'brief', group ?? 'base'];

// 그룹 전환 중 이전 그룹 내용 유지
export const useHomeBrief = (group?: string) =>
  useQuery({ ...useSessionQuery(), queryKey: briefKey(group), queryFn: () => api.home.brief(group) });

// 칩 전환의 즉시 표시용 모든 그룹 미리 받기
export const usePrefetchBriefs = (groups: string[] | undefined) => {
  const qc = useQueryClient();
  const { meta } = useSessionQuery();
  const keys = (groups ?? []).join(',');
  useEffect(() => {
    for (const g of keys ? keys.split(',') : []) qc.prefetchQuery({ queryKey: briefKey(g), queryFn: () => api.home.brief(g), meta: { sessionRevision: meta.sessionRevision } });
  }, [qc, keys, meta.sessionRevision]);
};
