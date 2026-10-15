/* tenants 도메인 — super-admin-api 연동 repository export.
 * mock 데이터는 API 쪽 mock 패키지가 반환한다 — 도메인별 DB 전환도 API 쪽에서 진행(ALPHA-515). */
import { realTenantsRepository } from './repository.real';
import type { TenantsRepository } from './repository';

export const tenantsRepository: TenantsRepository = realTenantsRepository;

export * from './types';
export * from './labels';
export type { TenantsRepository } from './repository';
