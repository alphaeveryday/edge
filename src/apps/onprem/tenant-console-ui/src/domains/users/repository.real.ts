/* users 도메인 — tenant-console-api 실연동 구현 */
import { apiClient } from '../../api/client';
import type { UsersRepository } from './repository';
import type { Member } from './types';

export const realUsersRepository: UsersRepository = {
  list: () => apiClient.get<Member[]>('/members'),
  invite: (email, role) => apiClient.post<void>('/members/invitations', { email, role }),
};
