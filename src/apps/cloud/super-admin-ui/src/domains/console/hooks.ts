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

/** facts가 실제로 본 날짜와 같은 축의 엔티티 해소율 최근 관측을 1분마다 갱신한다. */
export function useEntityResolutionTrend(date?: string, enabled = true) {
  return useQuery({
    queryKey: ['console', 'trends', 'entity-resolution', date ?? null],
    queryFn: () => consoleRepository.entityResolutionTrend(date),
    enabled,
    refetchInterval: 60_000,
  });
}

/** 실행 이력의 결과 축. 날짜별 facts N+1 대신 최근 일별 전용 API를 한 번만 조회한다. */
export function useIntradayAnalysisTrend(maxDate?: string, days = 30) {
  return useQuery({
    queryKey: ['console', 'trends', 'intraday-analysis', maxDate ?? null, days],
    queryFn: () => consoleRepository.intradayAnalysisTrend(maxDate, days),
    refetchInterval: 60_000,
  });
}

/**
 * v2 분석(전망·가격변동)의 일별 건수. facts 가 실제로 본 날짜로 끝나는 기간을 본다.
 * 이 엔드포인트가 없는 API 에서는 실패한다 — 화면은 그때 해당 카드·구역을 기록 없음으로 둔다.
 */
export function useAnalysisTrend(maxDate?: string, enabled = true, days = 30) {
  return useQuery({
    queryKey: ['console', 'trends', 'analysis', maxDate ?? null, days],
    queryFn: () => consoleRepository.analysisTrend(maxDate, days),
    enabled,
    refetchInterval: 60_000,
  });
}
