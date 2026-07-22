/* sources 도메인 — 실연동 구현 (현재 stub, super-admin-api 완성 시 배선) */
import { apiClient } from '../../api/client';
import type { SourcesRepository } from './repository';
import type { SourceReport } from './types';

export const realSourcesRepository: SourcesRepository = {
  report: () => apiClient.get<SourceReport>('/sources/report'),
};
