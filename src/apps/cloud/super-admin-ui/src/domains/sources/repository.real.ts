/* sources 도메인 — super-admin-api 연동 구현 (ALPHA-515, 런 주소지정 574) */
import { apiClient } from '../../api/client';
import type { SourcesRepository } from './repository';
import type {
  HoldingsImpact,
  MinuteStatus,
  NewsLineage,
  SourceGrid,
  SourceOverview,
  SourceReport,
} from './types';

export const realSourcesRepository: SourcesRepository = {
  /* runKey 는 `etf-daily:2026-07-27T15:40` 처럼 콜론이 들어가므로 반드시 인코딩한다. */
  report: (runKey) =>
    apiClient.get<SourceReport>(
      runKey ? `/sources/report?runKey=${encodeURIComponent(runKey)}` : '/sources/report',
    ),
  grid: (days) =>
    apiClient.get<SourceGrid>(days === undefined ? '/sources/grid' : `/sources/grid?days=${days}`),
  overview: () => apiClient.get<SourceOverview>('/sources/overview'),
  newsLineage: (date, limit, stage) => {
    const params = new URLSearchParams();
    if (date) params.set('date', date);
    if (limit !== undefined) params.set('limit', String(limit));
    if (stage) params.set('stage', stage);
    const qs = params.toString();
    return apiClient.get<NewsLineage>(qs ? `/sources/lineage/news?${qs}` : '/sources/lineage/news');
  },
  /* 빈 문자열을 부재로 접지 않는다 — ?runKey= 를 최신 런으로 위장하면 서버의 "지정 키
   * 미존재=404" 계약(오타를 빈 데이터로 오독 방지)이 우회된다 */
  holdingsImpact: (runKey) =>
    apiClient.get<HoldingsImpact>(
      runKey !== undefined
        ? `/sources/impact/holdings?runKey=${encodeURIComponent(runKey)}`
        : '/sources/impact/holdings',
    ),
  /* ⚠️ 인코딩은 이 파일의 형제들(`report`·`holdingsImpact` 의 `encodeURIComponent` ·
   * `newsLineage` 의 `URLSearchParams`)과 같아야 한다 — 축 E 부터 이 값이 **주소에서
   * 온 미검증 문자열**이다(`/ops/runs/:id?date=` → `useConsoleEvaluation` → 여기). 날것으로
   * 보간하면 `2026-08-10%26limit=1` 같은 입력이 `&` 로 풀려 **없던 파라미터**가 붙고, 서버는
   * 날짜 부분만 보고 200 을 준다 — 같은 값을 `console` 도메인의 `facts`
   * (`domains/console/repository.real.ts`)는 인코딩해 400 으로 거부하는데. 한 화면이 두 축을
   * 같은 값으로 물으므로 판별이 갈리면 안 된다. */
  minuteStatus: (date) =>
    apiClient.get<MinuteStatus>(
      date ? `/sources/minute?date=${encodeURIComponent(date)}` : '/sources/minute',
    ),
};
