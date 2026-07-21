/* screening 도메인 — config 를 보고 mock|real 중 하나를 export */
import { DATA_SOURCES } from '../../config/dataSources';
import { mockScreeningRepository } from './repository.mock';
import { realScreeningRepository } from './repository.real';
import type { ScreeningRepository } from './repository';

export const screeningRepository: ScreeningRepository =
  DATA_SOURCES.screening === 'real' ? realScreeningRepository : mockScreeningRepository;

export * from './types';
export type { ScreeningRepository } from './repository';
