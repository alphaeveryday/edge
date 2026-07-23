/* sources 도메인 — super-admin-api 연동 구현 (ALPHA-515) */
import { apiClient } from '../../api/client';
import type { SourcesRepository } from './repository';
import type { SourceReport } from './types';

export const realSourcesRepository: SourcesRepository = {
  report: () => apiClient.get<SourceReport>('/sources/report'),
};
