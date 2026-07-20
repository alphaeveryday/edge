/* members 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { Member, RoleSummary, InviteMemberInput } from './types';

export interface MembersRepository {
  /** 구성원 목록 */
  list(): Promise<Member[]>;
  /** 역할 요약 (역할별 인원·권한 범위) */
  listRoles(): Promise<RoleSummary[]>;
  /** 구성원 초대 — 생성된 구성원(초대됨 상태)을 반환 */
  invite(input: InviteMemberInput): Promise<Member>;
}
