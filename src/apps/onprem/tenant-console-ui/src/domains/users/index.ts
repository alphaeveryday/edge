/* users 도메인 — config 를 보고 mock|real 중 하나를 export */
import { DATA_SOURCES } from '../../config/dataSources';
import { mockUsersRepository } from './repository.mock';
import { realUsersRepository } from './repository.real';
import type { UsersRepository } from './repository';

export const usersRepository: UsersRepository =
  DATA_SOURCES.users === 'real' ? realUsersRepository : mockUsersRepository;

export * from './types';
export type { UsersRepository } from './repository';
