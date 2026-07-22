/* analyses 도메인 — config 를 보고 mock|real 중 하나를 export */
import { DATA_SOURCES } from '../../config/dataSources';
import { mockAnalysesRepository } from './repository.mock';
import { realAnalysesRepository } from './repository.real';
import type { AnalysesRepository } from './repository';

export const analysesRepository: AnalysesRepository =
  DATA_SOURCES.analyses === 'real' ? realAnalysesRepository : mockAnalysesRepository;

export * from './types';
export * from './labels';
export type { AnalysesRepository } from './repository';
