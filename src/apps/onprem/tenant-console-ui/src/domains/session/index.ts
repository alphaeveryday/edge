/* session 도메인 — config 를 보고 mock|real 중 하나를 export */
import { DATA_SOURCES } from '../../config/dataSources';
import { mockSessionRepository } from './repository.mock';
import { realSessionRepository } from './repository.real';
import type { SessionRepository } from './repository';

export const sessionRepository: SessionRepository =
  DATA_SOURCES.session === 'real' ? realSessionRepository : mockSessionRepository;

export * from './types';
export type { SessionRepository } from './repository';
