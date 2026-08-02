/* sources 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type {
  HoldingsImpact,
  MinuteStatus,
  NewsLineage,
  SourceGrid,
  SourceOverview,
  SourceReport,
} from './types';

export interface SourcesRepository {
  /** @param runKey 볼 런의 슬롯 키. 없으면 최신 런 */
  report(runKey?: string): Promise<SourceReport>;
  /** @param days 격자 조회 창(일). 없으면 서버 기본(30일) */
  grid(days?: number): Promise<SourceGrid>;
  /** Run Overview — 레인별 최신 런의 운영 요약(ALPHA-683) */
  overview(): Promise<SourceOverview>;
  /** 뉴스 계보(ALPHA-685). @param date KST 날짜(YYYY-MM-DD), 없으면 전체 누적. @param limit 표본 크기(서버 상한 200) */
  newsLineage(date?: string, limit?: number): Promise<NewsLineage>;
  /** holdings 결손 영향(ALPHA-686). @param runKey 없으면 최신 etf-daily 런 */
  holdingsImpact(runKey?: string): Promise<HoldingsImpact>;
  /** 장중 1분 파이프라인 요약(ALPHA-651). @param date 세션 날짜(KST), 없으면 오늘 */
  minuteStatus(date?: string): Promise<MinuteStatus>;
}
