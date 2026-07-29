/* analyses 도메인 — super-admin-api 연동 구현 (ALPHA-515) */
import { apiClient } from '../../api/client';
import type { AnalysesRepository } from './repository';
import type { Analysis } from './types';

export const realAnalysesRepository: AnalysesRepository = {
  list: () => apiClient.get<Analysis[]>('/analyses'),
  correct: (id, result, reason) =>
    apiClient.patch<void>(`/analyses/${id}/result`, { result, reason }),
  exclude: (id, reason) => apiClient.post<void>(`/analyses/${id}/exclude`, { reason }),
  restore: (id) => apiClient.post<void>(`/analyses/${id}/restore`),
};
