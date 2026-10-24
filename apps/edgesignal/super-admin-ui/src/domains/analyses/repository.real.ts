/* analyses 도메인 — super-admin-api 연동 구현 (ALPHA-515) */
import { apiClient } from '../../api/client';
import type { AnalysesRepository } from './repository';
import type { Analysis } from './types';

export const realAnalysesRepository: AnalysesRepository = {
  list: () => apiClient.get<Analysis[]>('/analyses'),
  invalidate: (id, reason) => apiClient.post<void>(`/analyses/${id}/invalidate`, { reason }),
};
