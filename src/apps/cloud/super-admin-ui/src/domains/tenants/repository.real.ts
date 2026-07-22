/* tenants 도메인 — 실연동 구현 (현재 stub, super-admin-api 완성 시 배선) */
import { apiClient } from '../../api/client';
import type { TenantsRepository } from './repository';
import type { Tenant } from './types';

export const realTenantsRepository: TenantsRepository = {
  list: () => apiClient.get<Tenant[]>('/tenants'),
  create: (input) => apiClient.post<void>('/tenants', input),
};
