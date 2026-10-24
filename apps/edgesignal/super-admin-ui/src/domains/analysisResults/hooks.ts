/* analysisResults 도메인 — 페이지가 사용하는 hook. 읽기 전용이다. */
import { useQuery } from '@tanstack/react-query';
import { analysisResultsRepository } from './index';
import type { AnalysisKind, AnalysisResultFilter } from './types';

export function useAnalysisResults(filter: AnalysisResultFilter) {
  return useQuery({
    queryKey: ['analysisResults', 'list', filter],
    queryFn: () => analysisResultsRepository.list(filter),
  });
}

export function useAnalysisResult(kind: AnalysisKind, id: string) {
  return useQuery({
    queryKey: ['analysisResults', 'detail', kind, id],
    queryFn: () => analysisResultsRepository.detail(kind, id),
  });
}
