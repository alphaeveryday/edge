/* tenants 도메인 — super-admin-api 연동 구현 (ALPHA-515) */
import { apiClient } from '../../api/client';
import type { TenantsRepository } from './repository';
import type { Tenant } from './types';

export const realTenantsRepository: TenantsRepository = {
  list: () => apiClient.get<Tenant[]>('/tenants'),
  create: (input) => apiClient.post<void>('/tenants', input),
};
