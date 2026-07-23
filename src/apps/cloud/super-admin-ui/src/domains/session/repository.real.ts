/* session 도메인 — super-admin-api 연동 구현 (ALPHA-515) */
import { apiClient } from '../../api/client';
import type { SessionRepository } from './repository';
import type { OperatorSession } from './types';

export const realSessionRepository: SessionRepository = {
  current: () => apiClient.get<OperatorSession>('/session'),
  updateDisplayName: (name) => apiClient.patch<void>('/session/profile', { name }),
};
