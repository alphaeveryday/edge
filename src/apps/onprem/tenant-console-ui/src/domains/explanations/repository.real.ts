/* explanations 도메인 — 실연동 구현 (현재 stub, tenant-console-api 완성 시 배선) */
import { apiClient } from '../../api/client';
import type { ExplanationsRepository } from './repository';
import type { Explanation, FeedStatus } from './types';

export const realExplanationsRepository: ExplanationsRepository = {
  list: () => apiClient.get<Explanation[]>('/explanations'),
  feedStatus: () => apiClient.get<FeedStatus>('/explanations/feed-status'),
  updateFinal: (id, final) => apiClient.patch<void>(`/explanations/${id}/final`, { final }),
  stop: (id) => apiClient.post<void>(`/explanations/${id}/stop`),
  moveToReview: (id) => apiClient.post<void>(`/explanations/${id}/move-to-review`),
  approve: (id, final, note) => apiClient.post<void>(`/explanations/${id}/approve`, { final, note }),
  reject: (id, note) => apiClient.post<void>(`/explanations/${id}/reject`, { note }),
  saveDraft: (id, final) => apiClient.patch<void>(`/explanations/${id}/draft`, { final }),
};
