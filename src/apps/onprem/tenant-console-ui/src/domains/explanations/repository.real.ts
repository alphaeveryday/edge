/* explanations 도메인 — tenant-console-api 실연동 구현 */
import { apiClient } from '../../api/client';
import type { ExplanationsRepository } from './repository';
import type { Explanation, FeedStatus } from './types';

export const realExplanationsRepository: ExplanationsRepository = {
  list: ({ limit, offset }) => apiClient.get<Explanation[]>(`/explanations?limit=${limit}&offset=${offset}`),
  get: (id) => apiClient.get<Explanation>(`/explanations/${encodeURIComponent(id)}`),
  statusCounts: () => apiClient.get<Record<string, number>>('/explanations/status-counts'),
  feedStatus: () => apiClient.get<FeedStatus>('/explanations/feed-status'),
  updateFinal: (id, final) => apiClient.patch<void>(`/explanations/${id}/final`, { final }),
  stop: (id, reason) => apiClient.post<void>(`/explanations/${id}/stop`, { reason }),
  moveToReview: (id) => apiClient.post<void>(`/explanations/${id}/move-to-review`),
};
