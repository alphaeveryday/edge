/* users 도메인 — tenant-console-api 실연동 구현 */
import { apiClient } from '../../api/client';
import type { UsersRepository } from './repository';
import type { Member } from './types';

export const realUsersRepository: UsersRepository = {
  list: () => apiClient.get<Member[]>('/members'),
  register: (input) => apiClient.post<Member>('/members', input),
  deactivate: (id) => apiClient.post<void>(`/members/${id}/deactivate`),
};
