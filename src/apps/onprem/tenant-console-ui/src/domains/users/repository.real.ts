/* users 도메인 — 실연동 구현 (현재 stub, tenant-console-api 완성 시 배선) */
import { apiClient } from '../../api/client';
import type { UsersRepository } from './repository';
import type { Member } from './types';

export const realUsersRepository: UsersRepository = {
  list: () => apiClient.get<Member[]>('/members'),
  invite: (email, role) => apiClient.post<void>('/members/invitations', { email, role }),
};
