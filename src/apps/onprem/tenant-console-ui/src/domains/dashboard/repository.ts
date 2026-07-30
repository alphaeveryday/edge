/* dashboard 도메인 — repository 인터페이스 */
import type { TrafficSummary } from './types';

export interface DashboardRepository {
  /** 제공 API 트래픽 요약 (최근 24시간) */
  traffic(): Promise<TrafficSummary>;
}
