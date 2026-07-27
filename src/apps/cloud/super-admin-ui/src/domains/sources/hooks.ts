/* sources 도메인 — 페이지가 사용하는 hook. */
import { useQuery } from '@tanstack/react-query';
import { sourcesRepository } from './index';

/**
 * @param runKey 볼 런의 슬롯 키. 없으면 최신 런.
 *
 * 캐시 키에 runKey 를 넣는다 — 빼면 런을 바꿔도 앞선 런의 캐시가 그대로 보인다.
 */
export function useSourceReport(runKey?: string) {
  return useQuery({
    queryKey: ['sources', runKey ?? null],
    queryFn: () => sourcesRepository.report(runKey),
  });
}
