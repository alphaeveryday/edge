import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { api } from '@/api';

const briefKey = (group?: string) => ['home', 'brief', group ?? 'base'];

// 그룹 전환 중 이전 그룹 내용 유지
export const useHomeBrief = (group?: string) =>
  useQuery({ queryKey: briefKey(group), queryFn: () => api.home.brief(group), placeholderData: keepPreviousData });

// 칩 전환의 즉시 표시용 모든 그룹 미리 받기
export const usePrefetchBriefs = (groups: string[] | undefined) => {
  const qc = useQueryClient();
  const keys = (groups ?? []).join(',');
  useEffect(() => {
    for (const g of keys ? keys.split(',') : []) qc.prefetchQuery({ queryKey: briefKey(g), queryFn: () => api.home.brief(g) });
  }, [qc, keys]);
};
