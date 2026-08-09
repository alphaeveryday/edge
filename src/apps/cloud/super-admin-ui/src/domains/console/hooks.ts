/* console 도메인 — 페이지가 사용하는 hook. */
import { useQuery } from '@tanstack/react-query';
import { consoleRepository } from './index';

/**
 * 규칙 엔진의 사실. 운영자가 띄워 두는 화면이라 1분마다 갱신한다(`useMinuteStatus` 와 같은 주기 —
 * 두 축이 다른 시각의 사실이면 한 화면에서 어긋난 판정이 조립된다).
 *
 * @param date 없으면 원장이 아는 가장 최근 날. 캐시 키에 넣는다 — 빼면 날짜를 바꿔도 앞선
 *             날짜의 캐시가 그대로 보인다.
 */
export function useConsoleFacts(date?: string) {
  return useQuery({
    queryKey: ['console', 'facts', date ?? null],
    queryFn: () => consoleRepository.facts(date),
    refetchInterval: 60_000,
  });
}
