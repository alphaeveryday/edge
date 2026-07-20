/* members 도메인 — real 구현 (tenant-console-api 연동)
 *
 * 현재는 stub 이다. 백엔드 members 엔드포인트가 완성되면 config/dataSources.ts 의
 * members 를 'real' 로 바꾸는 것만으로 이 구현이 활성화된다. 응답 DTO 가 도메인
 * 타입과 다르면 여기서 매핑한다 (mock·real 의 반환 타입은 동일해야 한다).
 */
import { apiClient } from '../../api/client';
import type { MembersRepository } from './repository';
import type { Member, RoleSummary, InviteMemberInput } from './types';

export const realMembersRepository: MembersRepository = {
  list() {
    return apiClient.get<Member[]>('/members');
  },

  listRoles() {
    return apiClient.get<RoleSummary[]>('/members/roles');
  },

  invite(input: InviteMemberInput) {
    return apiClient.post<Member>('/members/invitations', input);
  },
};
