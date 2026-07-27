/* dashboard 도메인 — tenant-console-api 연동 repository export */
import { realDashboardRepository } from './repository.real';
import type { DashboardRepository } from './repository';

export const dashboardRepository: DashboardRepository = realDashboardRepository;

export * from './types';
export type { DashboardRepository } from './repository';
