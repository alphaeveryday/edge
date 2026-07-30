/* dashboard 도메인 — tenant-console-api 실연동 구현 */
import { apiClient } from '../../api/client';
import type { DashboardRepository } from './repository';
import type { TrafficSummary } from './types';

export const realDashboardRepository: DashboardRepository = {
  traffic: () => apiClient.get<TrafficSummary>('/dashboard/traffic'),
};
