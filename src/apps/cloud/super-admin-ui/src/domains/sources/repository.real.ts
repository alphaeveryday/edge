/* sources 도메인 — super-admin-api 연동 구현 (ALPHA-515, 런 주소지정 574) */
import { apiClient } from '../../api/client';
import type { SourcesRepository } from './repository';
import type { HoldingsImpact, NewsLineage, SourceGrid, SourceOverview, SourceReport } from './types';

export const realSourcesRepository: SourcesRepository = {
  /* runKey 는 `etf-daily:2026-07-27T15:40` 처럼 콜론이 들어가므로 반드시 인코딩한다. */
  report: (runKey) =>
    apiClient.get<SourceReport>(
      runKey ? `/sources/report?runKey=${encodeURIComponent(runKey)}` : '/sources/report',
    ),
  grid: (days) =>
    apiClient.get<SourceGrid>(days === undefined ? '/sources/grid' : `/sources/grid?days=${days}`),
  overview: () => apiClient.get<SourceOverview>('/sources/overview'),
  newsLineage: (date, limit) => {
    const params = new URLSearchParams();
    if (date) params.set('date', date);
    if (limit !== undefined) params.set('limit', String(limit));
    const qs = params.toString();
    return apiClient.get<NewsLineage>(qs ? `/sources/lineage/news?${qs}` : '/sources/lineage/news');
  },
  holdingsImpact: (runKey) =>
    apiClient.get<HoldingsImpact>(
      runKey
        ? `/sources/impact/holdings?runKey=${encodeURIComponent(runKey)}`
        : '/sources/impact/holdings',
    ),
};
