/* sources 도메인 — 페이지가 사용하는 hook. */
import { useQuery } from '@tanstack/react-query';
import { sourcesRepository } from './index';
import type { NewsLineageStage } from './types';

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

/** 실행 격자(ALPHA-594). @param days 조회 창 — 캐시 키에 넣는 이유는 runKey 와 같다. */
export function useSourceGrid(days?: number) {
  return useQuery({
    queryKey: ['sources', 'grid', days ?? null],
    queryFn: () => sourcesRepository.grid(days),
  });
}

/** Run Overview(ALPHA-683) — 첫 화면. 운영자가 띄워 두는 화면이라 1분마다 갱신한다. */
export function useSourceOverview() {
  return useQuery({
    queryKey: ['sources', 'overview'],
    queryFn: () => sourcesRepository.overview(),
    refetchInterval: 60_000,
  });
}

/** 뉴스 계보(ALPHA-685·697). 캐시 키에 date·stage — 빼면 필터를 바꿔도 앞선 결과가 보인다. */
export function useNewsLineage(date?: string, limit?: number, stage?: NewsLineageStage) {
  return useQuery({
    queryKey: ['sources', 'lineage', 'news', date ?? null, limit ?? null, stage ?? null],
    queryFn: () => sourcesRepository.newsLineage(date, limit, stage),
  });
}

/** holdings 결손 영향(ALPHA-686). 캐시 키에 runKey — 런을 바꿔도 앞선 런 결과가 보이면 안 된다. */
export function useHoldingsImpact(runKey?: string) {
  return useQuery({
    queryKey: ['sources', 'impact', 'holdings', runKey ?? null],
    queryFn: () => sourcesRepository.holdingsImpact(runKey),
  });
}

/**
 * 장중 1분 파이프라인 요약(ALPHA-651). 장중 관측 화면이라 1분마다 갱신한다(overview 와 같은
 * 주기). 캐시 키에 date — 날짜를 바꿔도 앞선 날짜 결과가 보이면 안 된다.
 */
export function useMinuteStatus(date?: string) {
  return useQuery({
    queryKey: ['sources', 'minute', date ?? null],
    queryFn: () => sourcesRepository.minuteStatus(date),
    refetchInterval: 60_000,
  });
}
