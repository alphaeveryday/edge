/* users 도메인 — repository 인터페이스 (mock·real 공통 계약) */
import type { Member, MemberRole } from './types';

export interface UsersRepository {
  list(): Promise<Member[]>;
  invite(email: string, role: MemberRole): Promise<void>;
}
