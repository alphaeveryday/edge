/* screening 도메인 — 실연동 구현 (현재 stub, tenant-console-api 완성 시 배선) */
import { apiClient } from '../../api/client';
import type { ScreeningRepository } from './repository';
import type { AutoPublishCriteria, BannedWord } from './types';

export const realScreeningRepository: ScreeningRepository = {
  listWords: () => apiClient.get<BannedWord[]>('/screening/words'),
  addWord: (word) => apiClient.post<void>('/screening/words', word),
  toggleWord: (id) => apiClient.post<void>(`/screening/words/${id}/toggle`),
  getCriteria: () => apiClient.get<AutoPublishCriteria>('/screening/criteria'),
  updateCriteria: (patch) => apiClient.patch<void>('/screening/criteria', patch),
  getDisclaimer: () => apiClient.get<string>('/screening/disclaimer'),
  updateDisclaimer: (text) => apiClient.patch<void>('/screening/disclaimer', { text }),
};
