/* session 도메인 — tenant-console-api 실연동 구현 */
import { apiClient } from '../../api/client';
import type { SessionRepository } from './repository';
import type { SessionUser } from './types';

export const realSessionRepository: SessionRepository = {
  current: () => apiClient.get<SessionUser>('/session'),
  updateDisplayName: (name) => apiClient.patch<void>('/session/profile', { name }),
};
