/* scope 도메인 — config 를 보고 mock|real 중 하나를 export */
import { DATA_SOURCES } from '../../config/dataSources';
import { mockScopeRepository } from './repository.mock';
import { realScopeRepository } from './repository.real';
import type { ScopeRepository } from './repository';

export const scopeRepository: ScopeRepository =
  DATA_SOURCES.scope === 'real' ? realScopeRepository : mockScopeRepository;

export * from './types';
export type { ScopeRepository } from './repository';
