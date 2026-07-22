/* sources 도메인 — config 를 보고 mock|real 중 하나를 export */
import { DATA_SOURCES } from '../../config/dataSources';
import { mockSourcesRepository } from './repository.mock';
import { realSourcesRepository } from './repository.real';
import type { SourcesRepository } from './repository';

export const sourcesRepository: SourcesRepository =
  DATA_SOURCES.sources === 'real' ? realSourcesRepository : mockSourcesRepository;

export * from './types';
export type { SourcesRepository } from './repository';
