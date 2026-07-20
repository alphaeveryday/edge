/* dashboard 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { DashboardOverview } from './types';

export interface DashboardRepository {
  /** 대시보드 한 화면을 구성하는 복합 데이터를 한 번에 조회 */
  getOverview(): Promise<DashboardOverview>;
}
