/* explanations 도메인 — config 를 보고 mock|real 중 하나를 export */
import { DATA_SOURCES } from '../../config/dataSources';
import { mockExplanationsRepository } from './repository.mock';
import { realExplanationsRepository } from './repository.real';
import type { ExplanationsRepository } from './repository';

export const explanationsRepository: ExplanationsRepository =
  DATA_SOURCES.explanations === 'real' ? realExplanationsRepository : mockExplanationsRepository;

export * from './types';
export * from './labels';
export type { ExplanationsRepository } from './repository';
