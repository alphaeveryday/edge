/* screening 도메인 — tenant-console-api 실연동 구현 */
import { apiClient } from '../../api/client';
import type { ScreeningRepository } from './repository';
import type { AutoPublishCriteria, BannedWord, PolicyVersionSummary } from './types';

export const realScreeningRepository: ScreeningRepository = {
  listWords: () => apiClient.get<BannedWord[]>('/screening/words'),
  addWord: (word) => apiClient.post<void>('/screening/words', word),
  toggleWord: (id) => apiClient.post<void>(`/screening/words/${id}/toggle`),
  getCriteria: () => apiClient.get<AutoPublishCriteria>('/screening/criteria'),
  updateCriteria: (patch) => apiClient.patch<void>('/screening/criteria', patch),
  // 원시 문자열 응답은 JSON 파싱 계약과 어긋나 {text} 로 감싸 받는다 (API 계약)
  getDisclaimer: () => apiClient.get<{ text: string }>('/screening/disclaimer').then((r) => r.text),
  updateDisclaimer: (text) => apiClient.patch<void>('/screening/disclaimer', { text }),
  listVersions: () => apiClient.get<PolicyVersionSummary[]>('/screening/versions'),
};
