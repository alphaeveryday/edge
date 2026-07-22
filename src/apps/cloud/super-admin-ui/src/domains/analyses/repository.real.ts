/* analyses 도메인 — 실연동 구현 (현재 stub, super-admin-api 완성 시 배선) */
import { apiClient } from '../../api/client';
import type { AnalysesRepository } from './repository';
import type { Analysis } from './types';

export const realAnalysesRepository: AnalysesRepository = {
  list: () => apiClient.get<Analysis[]>('/analyses'),
  correct: (id, result) => apiClient.patch<void>(`/analyses/${id}/result`, { result }),
  exclude: (id) => apiClient.post<void>(`/analyses/${id}/exclude`),
  restore: (id) => apiClient.post<void>(`/analyses/${id}/restore`),
};
