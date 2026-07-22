/* tenants 도메인 — config 를 보고 mock|real 중 하나를 export */
import { DATA_SOURCES } from '../../config/dataSources';
import { mockTenantsRepository } from './repository.mock';
import { realTenantsRepository } from './repository.real';
import type { TenantsRepository } from './repository';

export const tenantsRepository: TenantsRepository =
  DATA_SOURCES.tenants === 'real' ? realTenantsRepository : mockTenantsRepository;

export * from './types';
export * from './labels';
export type { TenantsRepository } from './repository';
