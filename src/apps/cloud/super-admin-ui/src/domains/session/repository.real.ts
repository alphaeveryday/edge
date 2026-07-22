/* session 도메인 — 실연동 구현 (현재 stub, super-admin-api 완성 시 배선) */
import { apiClient } from '../../api/client';
import type { SessionRepository } from './repository';
import type { OperatorSession } from './types';

export const realSessionRepository: SessionRepository = {
  current: () => apiClient.get<OperatorSession>('/session'),
  updateDisplayName: (name) => apiClient.patch<void>('/session/profile', { name }),
};
