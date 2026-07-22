/* sources 도메인 — 페이지가 사용하는 hook. */
import { useQuery } from '@tanstack/react-query';
import { sourcesRepository } from './index';

export function useSourceReport() {
  return useQuery({ queryKey: ['sources'], queryFn: () => sourcesRepository.report() });
}
