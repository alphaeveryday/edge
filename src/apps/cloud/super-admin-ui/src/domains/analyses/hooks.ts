/* analyses 도메인 — 페이지가 사용하는 hook. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { analysesRepository } from './index';

const KEY = ['analyses'];

export function useAnalyses() {
  return useQuery({ queryKey: KEY, queryFn: () => analysesRepository.list() });
}

export function useAnalysis(id: string | undefined) {
  const query = useAnalyses();
  return { ...query, analysis: query.data?.find((a) => a.id === id) };
}

export function useAnalysisActions() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: KEY });

  const invalidateAnalysis = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      analysesRepository.invalidate(id, reason),
    onSuccess: invalidate,
  });

  return { invalidateAnalysis };
}
