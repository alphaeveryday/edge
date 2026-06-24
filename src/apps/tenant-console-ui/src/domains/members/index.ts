/* members 도메인 — 진입점.
 * config/dataSources.ts 의 스위치를 보고 mock|real repository 중 하나만 export 한다.
 * 페이지/hook 은 이 모듈만 의존한다 (구현체를 직접 import 하지 않는다).
 */
import { DATA_SOURCES } from '../../config/dataSources';
import type { MembersRepository } from './repository';
import { mockMembersRepository } from './repository.mock';
import { realMembersRepository } from './repository.real';

export const membersRepository: MembersRepository =
  DATA_SOURCES.members === 'real' ? realMembersRepository : mockMembersRepository;

export type { Member, RoleSummary, MemberRole, MemberStatus, InviteMemberInput } from './types';
export { useMembers } from './hooks';
